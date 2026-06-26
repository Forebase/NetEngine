"""Base abstraction for phase lifecycle hooks."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from netengine.handlers._base import BasePhaseHandler
    from netengine.handlers.context import PhaseContext


class HookPoint(str, Enum):
    """Phase lifecycle hook points."""

    BEFORE_PHASE = "before_phase"
    AFTER_PHASE_SUCCESS = "after_phase_success"
    AFTER_PHASE_FAILURE = "after_phase_failure"
    ON_PHASE_SKIP = "on_phase_skip"


class Hook(ABC):
    """Extension point in phase lifecycle."""

    @property
    @abstractmethod
    def hook_point(self) -> HookPoint:
        """Which lifecycle event this hook listens to."""
        pass

    @abstractmethod
    async def execute(
        self,
        phase_num: int,
        handler: "BasePhaseHandler",
        context: "PhaseContext",
        error: Optional[Exception] = None,
    ) -> None:
        """Execute hook logic.

        Exceptions are logged but do not block phase execution.
        """
        pass
