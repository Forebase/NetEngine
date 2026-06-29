import os
from typing import Any, Callable, Optional

from pydantic import ValidationError

from netengine.core.consumer_supervisor import ConsumerSupervisor
from netengine.core.docker_factory import create_docker_client
from netengine.core.phase_graph import PHASE_HANDLERS, PHASE_PREREQUISITES
from netengine.core.state import RuntimeState
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.app_handler import OrgAppsPhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.substrate import SubstrateHandler
from netengine.logging import get_logger
from netengine.monitoring.metrics import record_healthcheck_failure, record_phase
from netengine.phases.phase_ands import ANDsPhaseHandler
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler
from netengine.phases.phase_registries import RegistriesPhaseHandler
from netengine.phases.phase_services import ServicesPhaseHandler
from netengine.spec.loader import SpecLoadError
from netengine.spec.models import NetEngineSpec

# Re-exported for test patching: tests patch via "netengine.core.orchestrator.<Handler>"
__all__ = [
    "Orchestrator",
    "SubstrateHandler",
    "DNSHandler",
    "PKIPhaseHandler",
    "PlatformIdentityPhaseHandler",
    "RegistriesPhaseHandler",
    "InWorldIdentityPhaseHandler",
    "ANDsPhaseHandler",
    "ServicesPhaseHandler",
    "OrgAppsPhaseHandler",
]

logger = get_logger(__name__)


class Orchestrator:
    """Phase orchestration for NetEngine bootstrap.

    Executes phases 0-9 in sequence with proper dependency tracking,
    error handling, and state persistence.
    """

    # Expose the phase graph at class level for external consumers (tests, drift controller).
    PHASE_HANDLERS = PHASE_HANDLERS

    def __init__(self, spec: NetEngineSpec | dict[str, Any], mock_mode: Optional[bool] = None):
        """Initialize orchestrator with a validated NetEngine spec.

        Args:
            spec: Validated NetEngineSpec or raw YAML specification dictionary
            mock_mode: Override for mock mode. When None, reads NETENGINE_MOCK env var.
        """
        self.spec = self._normalize_spec(spec)
        self.runtime_state = RuntimeState.load()

        # Explicit arg wins over env var
        if mock_mode is not None:
            effective_mock = mock_mode
            docker_client: Optional[Any] = None
            if not effective_mock:
                docker_client, effective_mock = create_docker_client()
        else:
            docker_client, effective_mock = create_docker_client()

        self.mock_mode = effective_mock
        self.consumer_supervisor = ConsumerSupervisor()

        self.context = PhaseContext(
            spec=self.spec,
            runtime_state=self.runtime_state,
            logger=logger,
            docker_client=docker_client,
            mock_mode=effective_mock,
            consumer_supervisor=self.consumer_supervisor,
        )

        if effective_mock:
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
        required = PHASE_PREREQUISITES.get(phase_num, [])
        missing = [f for f in required if not getattr(self.runtime_state, f, None)]
        if missing:
            raise RuntimeError(
                f"Phase {phase_num} prerequisite(s) not satisfied: {', '.join(missing)}. "
                "Run earlier phases first."
            )

    async def execute_phases(
        self,
        up_to_phase: int = 9,
        on_phase_start: Optional[Callable[[int, str], None]] = None,
        on_phase_complete: Optional[Callable[[int, str], None]] = None,
        on_phase_skip: Optional[Callable[[int, str], None]] = None,
        on_phase_error: Optional[Callable[[int, str, Exception], None]] = None,
    ) -> None:
        """Execute phases 0 through up_to_phase.

        Args:
            up_to_phase: Highest phase number to execute (default 9, all phases)
            on_phase_start: Optional callback(phase_num, phase_name) called before each phase
            on_phase_complete: Optional callback(phase_num, phase_name) called on success
            on_phase_skip: Optional callback(phase_num, phase_name) called when phase is skipped
            on_phase_error: Optional callback(phase_num, phase_name, exc) called on failure

        Raises:
            RuntimeError: If any phase fails or dependency validation fails
        """
        from netengine.phase_labels import PHASE_LABELS

        if not self.runtime_state.world_spec:
            self.runtime_state.world_spec = self.spec.model_dump()
            self.runtime_state.save()

        for phase_num, handler_class in PHASE_HANDLERS:
            if phase_num > up_to_phase:
                break

            phase_name = PHASE_LABELS.get(str(phase_num), handler_class.__name__)
            handler = handler_class()

            if await handler.should_skip(self.context):
                logger.info(
                    f"Phase {phase_num}: {handler_class.__name__} already completed, skipping"
                )
                self._mark_phase_complete(phase_num, handler)
                if on_phase_skip:
                    on_phase_skip(phase_num, phase_name)
                continue

            self._check_prerequisites(phase_num)

            logger.info(f"Phase {phase_num}: {handler_class.__name__} starting")
            if on_phase_start:
                on_phase_start(phase_num, phase_name)
            try:
                with record_phase(phase_num):
                    await handler.execute(self.context)

                if not await handler.healthcheck(self.context):
                    record_healthcheck_failure(phase_num)
                    raise RuntimeError(f"Phase {phase_num} healthcheck failed")

                self._mark_phase_complete(phase_num, handler)
                self.runtime_state.save()
                self.runtime_state.sync_to_supabase()
                logger.info(f"Phase {phase_num} completed successfully")
                if on_phase_complete:
                    on_phase_complete(phase_num, phase_name)

            except Exception as e:
                logger.error(f"Phase {phase_num} failed: {e}")
                self.runtime_state.last_error = str(e)
                self.runtime_state.save()
                if on_phase_error:
                    on_phase_error(phase_num, phase_name, e)
                raise

    async def start_consumers(self) -> None:
        """Start all registered background consumer tasks."""
        await self.consumer_supervisor.start_all()

    def start_drift_detection(
        self,
        poll_interval_seconds: int = 30,
        max_drift_retries: int = 3,
        auto_heal: bool = True,
    ) -> None:
        """Start drift detection consumer.

        Registers a DriftDetectionController with ConsumerSupervisor so it runs
        as a background consumer with automatic restart on crash.

        Args:
            poll_interval_seconds: Time between healthchecks (default 30)
            max_drift_retries: Max self-heal attempts per phase
            auto_heal: If True, automatically re-apply diverged phases
        """
        from netengine.core.drift_controller import DriftDetectionController

        drift_controller = DriftDetectionController(
            orchestrator=self,
            poll_interval_seconds=poll_interval_seconds,
            max_drift_retries=max_drift_retries,
            auto_heal=auto_heal,
        )
        self.consumer_supervisor.register(
            "drift_detection",
            drift_controller.run,
        )

    def _mark_phase_complete(self, phase_num: int, handler: BasePhaseHandler) -> None:
        """Record phase completion.

        DNS is intentionally registered once: DNSHandler performs Phase 1
        and Phase 2 in one combined operation. Mark both complete here.
        """
        self.runtime_state.phase_completed[str(phase_num)] = True
        if isinstance(handler, DNSHandler):
            self.runtime_state.phase_completed["2"] = True

    def get_env_mock_mode(self) -> bool:
        """Read mock mode from environment variable."""
        return os.environ.get("NETENGINE_MOCK", "").lower() in ("1", "true", "yes")
