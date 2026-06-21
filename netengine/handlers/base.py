"""Base phase handler interface."""

from abc import ABC, abstractmethod

from netengine.handlers.context import PhaseContext


class BasePhaseHandler(ABC):
    """Abstract base for all phase handlers (M1-M8).

    All handlers must implement these methods. Handlers are async throughout.
    """

    @abstractmethod
    async def execute(self, context: PhaseContext) -> None:
        """Execute phase handler logic.

        Should populate the relevant output field in context.runtime_state.
        Must be idempotent (safe to call multiple times).

        Args:
            context: Dependency injection container with spec, state, logger, clients

        Raises:
            Exception: If execution fails; caught by orchestrator
        """
        pass

    @abstractmethod
    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify phase health and readiness.

        Returns True if phase is healthy and ready for downstream phases.
        Used for observability and diagnostic purposes.

        Args:
            context: Dependency injection container

        Returns:
            True if healthy, False otherwise
        """
        pass

    @abstractmethod
    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if this phase should be skipped.

        Returns True if phase should be skipped (e.g., already deployed).
        Used for conditional phase execution in reload scenarios.

        Args:
            context: Dependency injection container

        Returns:
            True if should skip, False if should execute
        """
        pass
