"""Drift detection and self-healing controller.

Continuously monitors running system state against desired spec.
Detects drift when handlers' healthcheck() returns False, and optionally
triggers self-healing by re-running execute() on drifted phases.
"""

import asyncio
import logs
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from netengine.core.orchestrator import Orchestrator
from netengine.events import factory as event_factory
from netengine.events.queues import Queue
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler

logger = logs.getLogger(__name__)


@dataclass
class DriftState:
    """Per-phase drift tracking state."""

    phase_num: int
    handler_name: str
    last_healthcheck_at: datetime
    is_drifted: bool
    drift_detected_at: Optional[datetime] = None
    self_healing_attempted: bool = False
    last_self_heal_at: Optional[datetime] = None
    last_self_heal_error: Optional[str] = None
    consecutive_drift_count: int = 0


class DriftDetectionController:
    """Continuous reconciliation consumer for drift detection and self-healing.

    Polls phase handlers' healthcheck() methods periodically to detect drift.
    When drift is detected, can optionally trigger self-healing by re-running
    handler execute() methods. Runs as a background consumer registered with
    ConsumerSupervisor.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        poll_interval_seconds: int = 30,
        max_drift_retries: int = 3,
        auto_heal: bool = True,
    ):
        """Initialize drift detection controller.

        Args:
            orchestrator: Orchestrator instance to poll and heal
            poll_interval_seconds: Time between healthchecks (default 30)
            max_drift_retries: Max self-heal attempts per phase before giving up
            auto_heal: If True, automatically re-run execute() on drifted phases
        """
        self.orchestrator = orchestrator
        self.poll_interval_seconds = poll_interval_seconds
        self.max_drift_retries = max_drift_retries
        self.auto_heal = auto_heal
        self.drift_states: dict[int, DriftState] = {}

    async def run(self) -> None:
        """Main drift detection loop.

        Runs continuously:
        1. Check healthcheck() on each completed phase
        2. Detect drift when healthcheck returns False
        3. Trigger self-healing if enabled
        4. Persist drift state to RuntimeState
        5. Sleep until next poll interval
        """
        logger.info(
            f"Drift detection starting (interval={self.poll_interval_seconds}s, "
            f"auto_heal={self.auto_heal})"
        )

        try:
            while True:
                await self._run_one_iteration()
                await asyncio.sleep(self.poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Drift detection stopped")
            raise

    async def _run_one_iteration(self) -> None:
        """Run one iteration of drift detection and healing."""
        try:
            drift_this_round = []

            for phase_num, handler_class in self.orchestrator.PHASE_HANDLERS:
                phase_key = str(phase_num)
                if not self.orchestrator.runtime_state.phase_completed.get(phase_key):
                    continue

                handler = handler_class()
                is_healthy = await self._check_phase_health(phase_num, handler)

                if not is_healthy:
                    drift_this_round.append(phase_num)
                    await self._emit_drift_event(
                        phase_num,
                        handler.__class__.__name__,
                        "drift.detected",
                        event_factory.drift_detected(
                            phase=phase_num,
                            handler=handler.__class__.__name__,
                            detected_at=datetime.now(UTC).isoformat(),
                        ),
                    )

            if drift_this_round and self.auto_heal:
                await self._trigger_self_healing(drift_this_round)

            self.orchestrator.runtime_state.last_drift_check_at = datetime.now(UTC)
            self.orchestrator.runtime_state.current_drift_phases = drift_this_round
            self.orchestrator.runtime_state.save()

        except Exception as e:
            logger.error(f"Drift detection iteration error: {e}", exc_info=True)
            await self._emit_drift_event(
                -1,
                "drift_controller",
                "drift.loop_error",
                event_factory.drift_loop_error(
                    error=str(e), error_at=datetime.now(UTC).isoformat()
                ),
            )

    async def _check_phase_health(self, phase_num: int, handler: BasePhaseHandler) -> bool:
        """Check healthcheck for a phase.

        Args:
            phase_num: Phase number
            handler: Handler instance

        Returns:
            True if healthy, False if drifted
        """
        try:
            is_healthy = await handler.healthcheck(self.orchestrator.context)
            self._update_drift_state(phase_num, handler.__class__.__name__, is_healthy)
            return bool(is_healthy)
        except Exception as e:
            logger.error(f"Phase {phase_num} healthcheck error: {e}")
            self._update_drift_state(phase_num, handler.__class__.__name__, False)
            return False

    def _update_drift_state(self, phase_num: int, handler_name: str, is_healthy: bool) -> None:
        """Update drift tracking state for a phase.

        Args:
            phase_num: Phase number
            handler_name: Handler class name
            is_healthy: Current health status
        """
        if phase_num not in self.drift_states:
            self.drift_states[phase_num] = DriftState(
                phase_num=phase_num,
                handler_name=handler_name,
                last_healthcheck_at=datetime.now(UTC),
                is_drifted=not is_healthy,
                drift_detected_at=datetime.now(UTC) if not is_healthy else None,
                consecutive_drift_count=1 if not is_healthy else 0,
            )
        else:
            state = self.drift_states[phase_num]
            state.last_healthcheck_at = datetime.now(UTC)

            if not is_healthy:
                if state.is_drifted:
                    state.consecutive_drift_count += 1
                else:
                    state.is_drifted = True
                    state.drift_detected_at = datetime.now(UTC)
                    state.consecutive_drift_count = 1
            else:
                if state.is_drifted:
                    state.is_drifted = False
                    state.consecutive_drift_count = 0

    async def _trigger_self_healing(self, drifted_phases: list[int]) -> None:
        """Trigger self-healing for drifted phases.

        Args:
            drifted_phases: List of phase numbers that drifted
        """
        healed_phases = set()

        for phase_num in sorted(drifted_phases):
            handler_class = next(
                (
                    handler
                    for pnum, handler in self.orchestrator.PHASE_HANDLERS
                    if pnum == phase_num
                ),
                None,
            )
            if handler_class is None:
                logger.warning(f"Phase {phase_num} handler not found")
                continue

            handler = handler_class()
            success, changed = await self._heal_phase(phase_num, handler)

            if success:
                healed_phases.add(phase_num)
                if changed:
                    await self._reheal_dependent_phases(phase_num, healed_phases)

    async def _heal_phase(self, phase_num: int, handler: BasePhaseHandler) -> tuple[bool, bool]:
        """Attempt to heal a drifted phase.

        Re-runs execute() and verifies with healthcheck().

        Args:
            phase_num: Phase number
            handler: Handler instance

        Returns:
            (success, changed_state) tuple
        """
        logger.warning(f"Drift detected on phase {phase_num} — attempting self-heal")
        drift_state = self.drift_states.get(phase_num)

        try:
            self.orchestrator._check_prerequisites(phase_num)
            await handler.execute(self.orchestrator.context)

            is_healthy = await handler.healthcheck(self.orchestrator.context)
            if not is_healthy:
                raise RuntimeError(f"Phase {phase_num} healthcheck still failing after re-run")

            logger.info(f"Phase {phase_num} self-healed successfully")
            await self._emit_drift_event(
                phase_num,
                handler.__class__.__name__,
                "drift.self_healed",
                event_factory.drift_self_healed(
                    phase=phase_num, healed_at=datetime.now(UTC).isoformat()
                ),
            )

            if drift_state:
                drift_state.self_healing_attempted = True
                drift_state.last_self_heal_at = datetime.now(UTC)
                drift_state.last_self_heal_error = None

            self.orchestrator.runtime_state.save()
            return True, True

        except Exception as e:
            logger.error(f"Phase {phase_num} self-heal failed: {e}")
            await self._emit_drift_event(
                phase_num,
                handler.__class__.__name__,
                "drift.self_heal_failed",
                event_factory.drift_self_heal_failed(
                    phase=phase_num, error=str(e), failed_at=datetime.now(UTC).isoformat()
                ),
            )

            if drift_state:
                drift_state.self_healing_attempted = True
                drift_state.last_self_heal_error = str(e)

            self.orchestrator.runtime_state.save()
            return False, False

    async def _reheal_dependent_phases(
        self, changed_phase_num: int, healed_phases: set[int]
    ) -> None:
        """Re-heal phases that depend on a changed phase.

        If phase N changed state, re-run phases that depend on N.

        Args:
            changed_phase_num: Phase number that changed
            healed_phases: Set of already-healed phases
        """
        for phase_num, handler_class in self.orchestrator.PHASE_HANDLERS:
            if phase_num in healed_phases:
                continue

            # Only re-heal phases that come after the changed phase and are completed
            if phase_num <= changed_phase_num:
                continue

            candidate_key = str(phase_num)
            if not self.orchestrator.runtime_state.phase_completed.get(candidate_key):
                continue

            handler = handler_class()
            try:
                if await handler.healthcheck(self.orchestrator.context):
                    continue
            except Exception:
                pass

            success, _ = await self._heal_phase(phase_num, handler)
            if success:
                healed_phases.add(phase_num)

    async def _emit_drift_event(
        self,
        phase_num: int,
        handler_name: str,
        event_type: str,
        event: EventEnvelope | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit a drift event.

        Args:
            phase_num: Phase number
            handler_name: Handler class name
            event_type: Event type (e.g., "drift.detected")
            payload: Event payload
        """
        if self.orchestrator.context.pgmq_client is None:
            logger.debug(f"pgmq_client not available, skipping event: {event_type}")
            return

        try:
            if event is None:
                event = event_factory.phase_event(
                    event_type=event_type,
                    emitted_by="drift_controller",
                    payload=payload or {},
                )
            await self.orchestrator.context.pgmq_client.send(Queue.DRIFT_EVENTS, event)
            logger.debug(f"Drift event emitted: {event_type}")
        except Exception as e:
            logger.error(f"Failed to emit drift event: {e}")
