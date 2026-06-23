import json
from typing import Any, Dict, Optional

from netengine.events.schema import EventEnvelope

MAX_RETRIES = 3


class PGMQClient:
    def __init__(self) -> None:
        self._db = None

    async def _get_db(self):
        if self._db is None:
            from netengine.core.supabase_client import get_db

            self._db = await get_db()
        return self._db

    async def send(self, queue_name: str, event: EventEnvelope) -> int:
        """Enqueue an event; returns message ID."""
        db = await self._get_db()
        payload = event.to_dict()
        result = await db.rpc(
            "pgmq_send", {"queue_name": queue_name, "message": json.dumps(payload)}
        ).execute()
        if not result.data:
            raise RuntimeError(f"pgmq_send returned no data for queue '{queue_name}'")
        return result.data[0]

    async def receive(self, queue_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        """Pop a message from the queue."""
        db = await self._get_db()
        result = await db.rpc(
            "pgmq_pop", {"queue_name": queue_name, "timeout": timeout}
        ).execute()
        if result.data:
            return result.data[0]
        return None

    async def delete(self, queue_name: str, msg_id: int) -> None:
        """Acknowledge and delete a processed message."""
        db = await self._get_db()
        await db.rpc("pgmq_delete", {"queue_name": queue_name, "msg_id": msg_id}).execute()

    async def read_by_id(self, queue_name: str, msg_id: int) -> Optional[Dict[str, Any]]:
        """Read a specific message by ID without consuming it."""
        db = await self._get_db()
        result = await db.rpc(
            "pgmq_read_by_id", {"queue_name": queue_name, "msg_id": msg_id}
        ).execute()
        if result.data:
            return result.data[0]
        return None

    async def archive_to_dlq(self, queue_name: str, msg_id: int, reason: str) -> None:
        """Re-queue with incremented retry count, or move to DLQ after MAX_RETRIES."""
        msg = await self.read_by_id(queue_name, msg_id)
        if not msg:
            return

        envelope = EventEnvelope(**json.loads(msg["message"]))
        await self.delete(queue_name, msg_id)

        if envelope.retry_count + 1 >= MAX_RETRIES:
            dlq_envelope = EventEnvelope(
                event_id=envelope.event_id,
                correlation_id=envelope.correlation_id,
                event_type=envelope.event_type,
                emitted_by=envelope.emitted_by,
                emitted_at=envelope.emitted_at,
                payload={**envelope.payload, "dlq_reason": reason},
                parent_event_id=envelope.parent_event_id,
                retry_count=envelope.retry_count + 1,
            )
            await self.send(f"{queue_name}_dlq", dlq_envelope)
        else:
            requeue_envelope = EventEnvelope(
                event_id=envelope.event_id,
                correlation_id=envelope.correlation_id,
                event_type=envelope.event_type,
                emitted_by=envelope.emitted_by,
                emitted_at=envelope.emitted_at,
                payload=envelope.payload,
                parent_event_id=envelope.parent_event_id,
                retry_count=envelope.retry_count + 1,
            )
            await self.send(queue_name, requeue_envelope)
