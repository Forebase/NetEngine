"""Metrics hook for phase timing and error tracking."""

import time
from typing import TYPE_CHECKING, Dict, Optional

from loguru import logger

from netengine.hooks.base import Hook, HookPoint

if TYPE_CHECKING:
    from netengine.handlers._base import BasePhaseHandler
    from netengine.handlers.context import PhaseContext


class MetricsHook(Hook):
    """Tracks phase execution timing and errors."""

    def __init__(self) -> None:
        self._phase_start_times: Dict[int, float] = {}

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.BEFORE_PHASE

    async def execute(
        self,
        phase_num: int,
        handler: "BasePhaseHandler",
        context: "PhaseContext",
        error: Optional[Exception] = None,
    ) -> None:
        """Record phase start time."""
        self._phase_start_times[phase_num] = time.time()


class SuccessMetricsHook(Hook):
    """Record successful phase duration."""

    def __init__(self) -> None:
        self._phase_start_times: Dict[int, float] = {}

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.AFTER_PHASE_SUCCESS

    async def execute(
        self,
        phase_num: int,
        handler: "BasePhaseHandler",
        context: "PhaseContext",
        error: Optional[Exception] = None,
    ) -> None:
        """Record successful phase completion time."""
        if phase_num in self._phase_start_times:
            elapsed = time.time() - self._phase_start_times.pop(phase_num)
            logger.info(f"Phase {phase_num} timing: {elapsed:.2f}s")


class FailureMetricsHook(Hook):
    """Record failed phase duration and error type."""

    def __init__(self) -> None:
        self._phase_start_times: Dict[int, float] = {}

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.AFTER_PHASE_FAILURE

    async def execute(
        self,
        phase_num: int,
        handler: "BasePhaseHandler",
        context: "PhaseContext",
        error: Optional[Exception] = None,
    ) -> None:
        """Record failed phase completion time and error."""
        if phase_num in self._phase_start_times:
            elapsed = time.time() - self._phase_start_times.pop(phase_num)
            error_type = error.__class__.__name__ if error else "Unknown"
            logger.warning(
                f"Phase {phase_num} failed after {elapsed:.2f}s: {error_type}"
            )
