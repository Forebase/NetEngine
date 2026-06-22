import asyncio
import logging
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


class ConsumerSupervisor:
    """Manages long-running consumer tasks with automatic restart on failure."""

    def __init__(self):
        self.tasks: Dict[str, asyncio.Task] = {}
        self.consumers: Dict[str, Callable] = {}

    def register(self, name: str, consumer_coro: Callable) -> None:
        """Register a consumer coroutine function."""
        self.consumers[name] = consumer_coro

    async def start_all(self) -> None:
        """Start all registered consumers."""
        for name, consumer_func in self.consumers.items():
            await self.start_consumer(name, consumer_func)

    async def start_consumer(self, name: str, consumer_func: Callable) -> None:
        """Start a single consumer with automatic restart on failure."""
        async def supervised_consumer():
            while True:
                try:
                    logger.info(f"Starting consumer: {name}")
                    await consumer_func()
                except asyncio.CancelledError:
                    logger.info(f"Consumer {name} cancelled")
                    break
                except Exception as e:
                    logger.error(f"Consumer {name} crashed: {e}. Restarting in 5s...")
                    await asyncio.sleep(5)

        task = asyncio.create_task(supervised_consumer())
        self.tasks[name] = task
        logger.info(f"Consumer {name} started (task ID: {task.name})")

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
        status = {}
        for name, task in self.tasks.items():
            if task.done():
                status[name] = "crashed" if task.exception() else "completed"
            else:
                status[name] = "running"
        return status
