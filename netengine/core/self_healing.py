"""Self-healing strategy for reconciliation.

Implements the logic for re-applying drifted phases and their dependencies.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from netengine.core.orchestrator import Orchestrator
from netengine.handlers._base import BasePhaseHandler

logger = logging.getLogger(__name__)


@dataclass
class SelfHealResult:
    """Result of a self-healing attempt."""

    phase_num: int
    handler_name: str
    success: bool
    changed_state: bool = False
    error: Optional[str] = None


class SelfHealingStrategy:
    """Logic for re-applying drifted phases and their dependencies."""

    def __init__(self, orchestrator: Orchestrator):
        """Initialize self-healing strategy.

        Args:
            orchestrator: Orchestrator instance to use for healing
        """
        self.orchestrator = orchestrator

    async def heal_phase(
        self,
        phase_num: int,
        handler: BasePhaseHandler,
    ) -> SelfHealResult:
        """Heal a drifted phase by re-running execute().

        Re-runs handler.execute() and verifies with healthcheck().

        Args:
            phase_num: Phase number
            handler: Handler instance

        Returns:
            SelfHealResult with success/error details
        """
        logger.warning(f"Attempting to heal phase {phase_num}")

        try:
            self.orchestrator._check_prerequisites(phase_num)
            await handler.execute(self.orchestrator.context)

            is_healthy = await handler.healthcheck(self.orchestrator.context)
            if not is_healthy:
                raise RuntimeError(f"Phase {phase_num} healthcheck still failing after re-run")

            logger.info(f"Phase {phase_num} healed successfully")
            self.orchestrator.runtime_state.save()

            return SelfHealResult(
                phase_num=phase_num,
                handler_name=handler.__class__.__name__,
                success=True,
                changed_state=True,
                error=None,
            )

        except Exception as e:
            logger.error(f"Phase {phase_num} healing failed: {e}")
            self.orchestrator.runtime_state.save()

            return SelfHealResult(
                phase_num=phase_num,
                handler_name=handler.__class__.__name__,
                success=False,
                changed_state=False,
                error=str(e),
            )

    async def heal_dependent_phases(
        self,
        changed_phase_num: int,
        healed_phases: set[int],
    ) -> list[SelfHealResult]:
        """Re-heal phases that depend on a changed phase.

        If phase N changed state, re-run phases that depend on N.

        Args:
            changed_phase_num: Phase number that changed
            healed_phases: Set of phases already healed

        Returns:
            List of SelfHealResult for dependent phases
        """
        results = []
        phase_key = str(changed_phase_num)

        if not self.orchestrator.runtime_state.phase_completed.get(phase_key):
            return results

        for phase_num, handler_class in self.orchestrator.PHASE_HANDLERS:
            if phase_num in healed_phases:
                continue

            handler = handler_class()
            try:
                if await handler.healthcheck(self.orchestrator.context):
                    continue
            except Exception:
                pass

            result = await self.heal_phase(phase_num, handler)
            results.append(result)

            if result.success:
                healed_phases.add(phase_num)

        return results
