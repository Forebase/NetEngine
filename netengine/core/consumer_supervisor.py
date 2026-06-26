import asyncio
from typing import Any, Callable, Coroutine, Dict

from netengine.logging import get_logger

logger = get_logger(__name__)

_BACKOFF_BASE = 5
_BACKOFF_MAX = 60


class ConsumerSupervisor:
    """Manages long-running consumer tasks with automatic restart on failure."""

    def __init__(self) -> None:
        self.tasks: Dict[str, asyncio.Task[Any]] = {}
        self.consumers: Dict[str, Callable[[], Coroutine[Any, Any, None]]] = {}

    def register(self, name: str, consumer_coro: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register a consumer coroutine function."""
        self.consumers[name] = consumer_coro

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
                    delay = _BACKOFF_BASE  # reset on clean exit
                except asyncio.CancelledError:
                    logger.info(f"Consumer {name} cancelled")
                    break
                except Exception as e:
                    logger.error(f"Consumer {name} crashed: {e}. Restarting in {delay}s...")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, _BACKOFF_MAX)

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
        """Get status of all consumers."""
        status: Dict[str, str] = {}
        for name, task in self.tasks.items():
            if task.done():
                status[name] = "crashed" if task.exception() else "completed"
            else:
                status[name] = "running"
        return status
