"""Dead-letter queue watch and guarded replay workers."""

import asyncio
import json
from typing import Any

from netengine.core.pgmq_client import PGMQClient
from netengine.events.queues import Queue
from netengine.events.schema import EventEnvelope
from netengine.logs import get_logger

logger = get_logger(__name__)


class DLQReplayWorker:
    """Watch a DLQ and replay only messages explicitly marked resolved.

    Operators or remediation jobs must set ``payload.dlq_resolved`` to true before
    this worker will move an event back to its primary queue. This avoids hot-loop
    replay of unresolved poison messages.
    """

    def __init__(
        self,
        pgmq: PGMQClient,
        primary_queue: Queue,
        dlq_queue: Queue,
        *,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._pgmq = pgmq
        self._primary_queue = primary_queue
        self._dlq_queue = dlq_queue
        self._poll_interval_seconds = poll_interval_seconds

    async def run(self) -> None:
        logger.info(
            "Starting DLQ replay worker for %s -> %s", self._dlq_queue, self._primary_queue
        )
        while True:
            replayed = await self.replay_once()
            if not replayed:
                await asyncio.sleep(self._poll_interval_seconds)

    async def replay_once(self) -> bool:
        """Attempt one guarded replay. Returns True when a message was replayed."""
        msg = await self._pgmq.receive(self._dlq_queue)
        if not msg:
            return False

        envelope = self._decode(msg)
        if not envelope.payload.get("dlq_resolved"):
            logger.warning(
                "Leaving unresolved DLQ message %s on %s; payload.dlq_resolved is not true",
                msg["msg_id"],
                self._dlq_queue,
            )
            return False

        replay_payload: dict[str, Any] = dict(envelope.payload)
        replay_payload.pop("dlq_reason", None)
        replay_payload.pop("dlq_resolved", None)
        replay = EventEnvelope(
            event_id=envelope.event_id,
            correlation_id=envelope.correlation_id,
            event_type=envelope.event_type,
            emitted_by=envelope.emitted_by,
            emitted_at=envelope.emitted_at,
            payload=replay_payload,
            parent_event_id=envelope.parent_event_id,
            retry_count=0,
        )
        await self._pgmq.send(self._primary_queue, replay)
        await self._pgmq.delete(self._dlq_queue, msg["msg_id"])
        logger.info("Replayed resolved DLQ message %s to %s", msg["msg_id"], self._primary_queue)
        return True

    @staticmethod
    def _decode(msg: dict[str, Any]) -> EventEnvelope:
        payload = json.loads(msg["message"])
        return EventEnvelope(**payload)
