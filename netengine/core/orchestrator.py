import logging
from typing import Any, List, Type

from pydantic import ValidationError

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


class Orchestrator:
    """Phase orchestration for NetEngine bootstrap.

    Executes phases 0-8 in sequence with proper dependency tracking,
    error handling, and state persistence.
    """

    # Phase registry: (phase_number, handler_class)
    PHASE_HANDLERS: List[tuple[int, Type[BasePhaseHandler]]] = [
        (0, SubstrateHandler),
        (1, DNSHandler),
        (2, DNSHandler),  # DNS phases 1-2 in same handler
        (3, PKIPhaseHandler),
        (4, PlatformIdentityPhaseHandler),
        (5, RegistriesPhaseHandler),
        (6, InWorldIdentityPhaseHandler),
        (7, ANDsPhaseHandler),
        (8, ServicesPhaseHandler),
    ]

    def __init__(self, spec: NetEngineSpec | dict[str, Any]):
        """Initialize orchestrator with a validated NetEngine spec.

        Args:
            spec: Validated NetEngineSpec or raw YAML specification dictionary
        """
        self.spec = self._normalize_spec(spec)
        self.runtime_state = RuntimeState.load()
        self.context = PhaseContext(
            spec=self.spec,
            runtime_state=self.runtime_state,
            logger=logger,
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

            # Instantiate handler
            handler = handler_class()

            # Check if should skip (already executed)
            if await handler.should_skip(self.context):
                logger.info(
                    f"Phase {phase_num}: {handler_class.__name__} already completed, skipping"
                )
                self.runtime_state.phase_completed[str(phase_num)] = True
                continue

            logger.info(f"Phase {phase_num}: {handler_class.__name__} starting")
            try:
                # Execute phase
                await handler.execute(self.context)

                # Healthcheck
                if not await handler.healthcheck(self.context):
                    raise RuntimeError(f"Phase {phase_num} healthcheck failed")

                # Mark complete
                self.runtime_state.phase_completed[str(phase_num)] = True
                self.runtime_state.save()
                logger.info(f"Phase {phase_num} completed successfully")

            except Exception as e:
                logger.error(f"Phase {phase_num} failed: {e}")
                self.runtime_state.error = str(e)
                self.runtime_state.save()
                raise
