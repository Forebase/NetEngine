"""Logging hook for phase lifecycle events."""

from typing import TYPE_CHECKING, Optional

from loguru import logger

from netengine.hooks.base import Hook, HookPoint

if TYPE_CHECKING:
    from netengine.handlers._base import BasePhaseHandler
    from netengine.handlers.context import PhaseContext


class LoggingHook(Hook):
    """Logs phase lifecycle events with structured context."""

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
        """Log phase events."""
        handler_name = handler.__class__.__name__

        if context.runtime_state.correlation_id:
            logger.info(
                f"Phase {phase_num}: {handler_name} | "
                f"correlation_id={context.runtime_state.correlation_id}"
            )
        else:
            logger.info(f"Phase {phase_num}: {handler_name} started")


class SuccessLoggingHook(Hook):
    """Logs successful phase completion."""

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
        """Log successful phase completion."""
        handler_name = handler.__class__.__name__
        logger.info(f"Phase {phase_num}: {handler_name} completed successfully")


class FailureLoggingHook(Hook):
    """Logs phase failures with error details."""

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
        """Log phase failure."""
        handler_name = handler.__class__.__name__
        error_msg = str(error) if error else "Unknown error"
        logger.error(f"Phase {phase_num}: {handler_name} failed: {error_msg}")


class SkipLoggingHook(Hook):
    """Logs skipped phases."""

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.ON_PHASE_SKIP

    async def execute(
        self,
        phase_num: int,
        handler: "BasePhaseHandler",
        context: "PhaseContext",
        error: Optional[Exception] = None,
    ) -> None:
        """Log skipped phase."""
        handler_name = handler.__class__.__name__
        logger.info(f"Phase {phase_num}: {handler_name} already completed, skipping")
