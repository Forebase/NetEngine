import logging
from typing import Any, List, Type

from pydantic import ValidationError

from netengine.core.consumer_supervisor import ConsumerSupervisor
from netengine.core.state import RuntimeState
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.substrate import SubstrateHandler
from netengine.phases.phase_ands import ANDsPhaseHandler
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler
from netengine.phases.phase_registries import RegistriesPhaseHandler
from netengine.phases.phase_services import ServicesPhaseHandler
from netengine.spec.loader import SpecLoadError
from netengine.spec.models import NetEngineSpec

logger = logging.getLogger(__name__)

# Required runtime_state field(s) that must be truthy before a phase runs.
_PHASE_PREREQUISITES: dict[int, list[str]] = {
    3: ["dns_output"],
    4: ["pki_bootstrapped"],
    5: ["identity_platform_output"],
    6: ["world_registry_output", "domain_registry_output"],
    7: ["identity_inworld_output"],
    8: ["ands_output"],
}


class Orchestrator:
    """Phase orchestration for NetEngine bootstrap.

    Executes phases 0-8 in sequence with proper dependency tracking,
    error handling, and state persistence.
    """

    # Phase registry: (phase_number, handler_class)
    # DNS is a single combined milestone: one handler performs both Phase 1
    # (root/platform zones) and Phase 2 (TLD setup), then marks both complete.
    PHASE_HANDLERS: List[tuple[int, Type[BasePhaseHandler]]] = [
        (0, SubstrateHandler),
        (1, DNSHandler),
        (3, PKIPhaseHandler),
        (4, PlatformIdentityPhaseHandler),
        (5, RegistriesPhaseHandler),
        (6, InWorldIdentityPhaseHandler),
        (7, ANDsPhaseHandler),
        (8, ServicesPhaseHandler),
    ]

    def __init__(self, spec: NetEngineSpec | dict[str, Any], mock_mode: bool = False):
        """Initialize orchestrator with a validated NetEngine spec.

        Args:
            spec: Validated NetEngineSpec or raw YAML specification dictionary
            mock_mode: When True, no real Docker/DNS/PKI calls are made
        """
        self.spec = self._normalize_spec(spec)
        self.mock_mode = mock_mode
        self.runtime_state = RuntimeState.load()
        self.consumer_supervisor = ConsumerSupervisor()
        self.context = PhaseContext(
            spec=self.spec,
            runtime_state=self.runtime_state,
            logger=logger,
            consumer_supervisor=self.consumer_supervisor,
        )

        if mock_mode:
            logger.warning(
                "WARNING: running in mock mode — no real infrastructure will be created. "
                "All phase outputs are simulated."
            )

    @staticmethod
    def _normalize_spec(spec: NetEngineSpec | dict[str, Any]) -> NetEngineSpec:
        """Normalize raw spec dictionaries into the canonical spec model."""
        if isinstance(spec, NetEngineSpec):
            return spec

        if not isinstance(spec, dict):
            raise SpecLoadError("Spec must be a NetEngineSpec or YAML object (dict)")

        try:
            return NetEngineSpec.model_validate(spec)
        except ValidationError as e:
            raise SpecLoadError(f"Spec validation failed: {e}") from e

    def _check_prerequisites(self, phase_num: int) -> None:
        """Raise RuntimeError if required prior-phase outputs are absent."""
        required = _PHASE_PREREQUISITES.get(phase_num, [])
        missing = [field for field in required if not getattr(self.runtime_state, field, None)]
        if missing:
            raise RuntimeError(
                f"Phase {phase_num} prerequisite(s) not satisfied: {', '.join(missing)}. "
                "Run earlier phases first."
            )

    async def execute_phases(self, up_to_phase: int = 8) -> None:
        """Execute phases 0 through up_to_phase.

        Args:
            up_to_phase: Highest phase number to execute (default 8, all phases)

        Raises:
            RuntimeError: If any phase fails or dependency validation fails
        """
        for phase_num, handler_class in self.PHASE_HANDLERS:
            if phase_num > up_to_phase:
                break

            handler = handler_class()

            if await handler.should_skip(self.context):
                logger.info(
                    f"Phase {phase_num}: {handler_class.__name__} already completed, skipping"
                )
                self._mark_phase_complete(phase_num, handler)
                continue

            self._check_prerequisites(phase_num)

            logger.info(f"Phase {phase_num}: {handler_class.__name__} starting")
            try:
                await handler.execute(self.context)

                if not await handler.healthcheck(self.context):
                    raise RuntimeError(f"Phase {phase_num} healthcheck failed")

                self._mark_phase_complete(phase_num, handler)
                self.runtime_state.save()
                logger.info(f"Phase {phase_num} completed successfully")

            except Exception as e:
                logger.error(f"Phase {phase_num} failed: {e}")
                self.runtime_state.last_error = str(e)
                self.runtime_state.save()
                raise

    async def start_consumers(self) -> None:
        """Start all registered background consumers."""
        await self.consumer_supervisor.start_all()

    def _mark_phase_complete(self, phase_num: int, handler: BasePhaseHandler) -> None:
        """Record user-facing phase completion for a completed handler.

        DNS is intentionally registered once because it performs Phase 1 and
        Phase 2 in one combined operation. Preserve user-facing progress by
        marking both phase numbers complete when that combined milestone is
        healthy or skipped.
        """
        self.runtime_state.phase_completed[str(phase_num)] = True
        if isinstance(handler, DNSHandler):
            self.runtime_state.phase_completed["2"] = True
