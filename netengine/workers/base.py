"""Base abstraction for pgmq queue workers."""

import asyncio
import json
from abc import ABC, abstractmethod

from loguru import logger

from netengine.connectors import get_pgmq_connector
from netengine.events.schema import EventEnvelope


class QueueWorker(ABC):
    """Typed consumer for a pgmq queue with supervised lifecycle."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique worker name (e.g., 'dns_updates_consumer')."""
        pass

    @property
    @abstractmethod
    def queue_name(self) -> str:
        """PGMQ queue to consume from."""
        pass

    @abstractmethod
    async def handle_message(self, message: EventEnvelope) -> None:
        """Process a single message.

        Raise exception to archive to DLQ after max retries.
        ConsumerSupervisor handles deletion on success.
        """
        pass

    async def run(self) -> None:
        """Consumer loop with error handling and backoff.

        Instantiated and supervised by ConsumerSupervisor.
        Do not override; use handle_message instead.
        """
        pgmq = await get_pgmq_connector()

        while True:
            try:
                msg = await pgmq.receive(self.queue_name, timeout=5)
                if not msg:
                    await asyncio.sleep(1)
                    continue

                try:
                    envelope = EventEnvelope(**json.loads(msg["message"]))
                    await self.handle_message(envelope)
                    await pgmq.delete(self.queue_name, msg["msg_id"])
                except Exception as e:
                    logger.error(
                        f"Worker {self.name} error processing message {msg['msg_id']}: {e}"
                    )
                    await pgmq.archive_to_dlq(
                        self.queue_name, msg["msg_id"], str(e)
                    )
            except Exception as e:
                logger.error(f"Worker {self.name} connection error: {e}")
                await asyncio.sleep(5)
