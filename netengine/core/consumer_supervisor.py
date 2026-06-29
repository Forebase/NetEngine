import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine, Dict, Literal

from netengine.logs import get_logger

logger = get_logger(__name__)

_BACKOFF_BASE = 5
_BACKOFF_MAX = 60


WorkerState = Literal["registered", "running", "failed", "disabled", "stopped", "completed"]


@dataclass
class WorkerStatus:
    """Structured status for a supervised background worker."""

    name: str
    state: WorkerState
    restarts: int = 0
    last_error: str | None = None
    disabled_reason: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    last_crashed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConsumerSupervisor:
    """Manages long-running consumer tasks with automatic restart on failure."""

    def __init__(self) -> None:
        self.tasks: Dict[str, asyncio.Task[Any]] = {}
        self.consumers: Dict[str, Callable[[], Coroutine[Any, Any, None]]] = {}
        self._statuses: Dict[str, WorkerStatus] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def register(self, name: str | Any, consumer_coro: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register a consumer coroutine function with a stable operator-visible name."""
        stable_name = str(name)
        self.consumers[stable_name] = consumer_coro
        self._statuses[stable_name] = WorkerStatus(name=stable_name, state="registered")

    def register_disabled(self, name: str | Any, reason: str) -> None:
        """Record a disabled worker so operators can see missing dependencies."""
        stable_name = str(name)
        self.consumers.pop(stable_name, None)
        self._statuses[stable_name] = WorkerStatus(
            name=stable_name, state="disabled", disabled_reason=reason
        )

    async def start_all(self) -> None:
        """Start all registered consumers."""
        for name, consumer_func in self.consumers.items():
            await self.start_consumer(name, consumer_func)

    async def start_consumer(
        self, name: str, consumer_func: Callable[[], Coroutine[Any, Any, None]]
    ) -> None:
        """Start a single consumer with automatic restart and exponential backoff."""

        async def supervised_consumer() -> None:
            delay = _BACKOFF_BASE
            while True:
                try:
                    logger.info(f"Starting consumer: {name}")
                    await consumer_func()
                    status = self._statuses.setdefault(name, WorkerStatus(name=name, state="completed"))
                    status.state = "completed"
                    status.stopped_at = self._now()
                    delay = _BACKOFF_BASE  # reset on clean exit
                except asyncio.CancelledError:
                    status = self._statuses.setdefault(name, WorkerStatus(name=name, state="stopped"))
                    status.state = "stopped"
                    status.stopped_at = self._now()
                    logger.info(f"Consumer {name} cancelled")
                    break
                except Exception as e:
                    status = self._statuses.setdefault(name, WorkerStatus(name=name, state="failed"))
                    status.state = "failed"
                    status.last_error = str(e)
                    status.last_crashed_at = self._now()
                    status.restarts += 1
                    logger.error(f"Consumer {name} crashed: {e}. Restarting in {delay}s...")
                    await asyncio.sleep(delay)
                    status.state = "running"
                    delay = min(delay * 2, _BACKOFF_MAX)

        if name in self._statuses and self._statuses[name].state == "disabled":
            logger.info(f"Consumer {name} not started because it is disabled")
            return
        self._statuses[name] = WorkerStatus(name=name, state="running", started_at=self._now())
        task: asyncio.Task[None] = asyncio.create_task(supervised_consumer())
        self.tasks[name] = task
        logger.info(f"Consumer {name} started")

    async def stop_all(self) -> None:
        """Stop all consumers gracefully."""
        for name, task in self.tasks.items():
            logger.info(f"Stopping consumer: {name}")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_status(self) -> Dict[str, str]:
        """Get legacy status of all consumers."""
        return {name: status.state for name, status in self._statuses.items()}

    def get_structured_status(self) -> dict[str, dict[str, Any]]:
        """Get structured operator/doctor status for all registered workers."""
        for name, task in self.tasks.items():
            status = self._statuses.setdefault(name, WorkerStatus(name=name, state="registered"))
            if task.done() and status.state == "running":
                exc = task.exception()
                status.state = "failed" if exc else "completed"
                status.last_error = str(exc) if exc else None
                status.stopped_at = self._now()
        return {name: status.to_dict() for name, status in self._statuses.items()}
