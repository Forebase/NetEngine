"""Hook system for phase lifecycle events."""

from typing import TYPE_CHECKING, Dict, List

from loguru import logger

from netengine.hooks.base import Hook, HookPoint
from netengine.hooks.logging_hook import (
    FailureLoggingHook,
    LoggingHook,
    SkipLoggingHook,
    SuccessLoggingHook,
)
from netengine.hooks.metrics_hook import (
    FailureMetricsHook,
    SuccessMetricsHook,
)

if TYPE_CHECKING:
    from netengine.handlers._base import BasePhaseHandler
    from netengine.handlers.context import PhaseContext


class HookRegistry:
    """Manages phase lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: Dict[HookPoint, List[Hook]] = {hp: [] for hp in HookPoint}
        self._initialize_default_hooks()

    def _initialize_default_hooks(self) -> None:
        """Register built-in hooks."""
        self.register(LoggingHook())
        self.register(SuccessLoggingHook())
        self.register(FailureLoggingHook())
        self.register(SkipLoggingHook())

    def register(self, hook: Hook) -> None:
        """Register a hook for a lifecycle point."""
        self._hooks[hook.hook_point].append(hook)
        logger.debug(f"Registered hook: {hook.__class__.__name__} -> {hook.hook_point}")

    async def execute_hooks(
        self,
        point: HookPoint,
        phase_num: int,
        handler: "BasePhaseHandler",
        context: "PhaseContext",
        error: Exception = None,
    ) -> None:
        """Execute all hooks registered for a lifecycle point.

        Exceptions are logged but do not block phase execution.
        """
        for hook in self._hooks[point]:
            try:
                await hook.execute(phase_num, handler, context, error)
            except Exception as e:
                logger.warning(
                    f"Hook {hook.__class__.__name__} failed: {e}"
                )


__all__ = [
    "Hook",
    "HookPoint",
    "HookRegistry",
    "LoggingHook",
    "SuccessLoggingHook",
    "FailureLoggingHook",
    "SkipLoggingHook",
    "SuccessMetricsHook",
    "FailureMetricsHook",
]
