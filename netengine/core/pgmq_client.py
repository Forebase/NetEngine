import json
from typing import Any, Dict, Optional

from netengine.core.supabase_client import get_supabase
from netengine.events.schema import EventEnvelope


class PGMQClient:
    def __init__(self):
        self.supabase = get_supabase()

    async def send(self, queue_name: str, event: EventEnvelope) -> int:
        """Enqueue an event; returns message ID."""
        payload = event.to_dict()
        # Supabase pgmq uses `pgmq.send` function.
        # We'll call the RPC function `pgmq_send` (needs to be created in Supabase).
        # Alternatively, use raw SQL via REST.
        # For MVP, we'll assume a Postgres function exists: pgmq.send(queue_name, message_json)
        result = await self.supabase.rpc(
            "pgmq_send", {"queue_name": queue_name, "message": json.dumps(payload)}
        ).execute()
        return result.data[0]  # msg_id

    async def receive(self, queue_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        """Pop a message from the queue."""
        result = await self.supabase.rpc(
            "pgmq_pop", {"queue_name": queue_name, "timeout": timeout}
        ).execute()
        if result.data:
            return result.data[0]
        return None

    async def delete(self, queue_name: str, msg_id: int) -> None:
        """Acknowledge and delete a processed message."""
        await self.supabase.rpc(
            "pgmq_delete", {"queue_name": queue_name, "msg_id": msg_id}
        ).execute()

    async def archive_to_dlq(self, queue_name: str, msg_id: int, reason: str) -> None:
        """Move a failed message to the DLQ after max retries."""
        # First, pop the message to get its payload
        msg = await self.receive(queue_name)
        if msg and msg["msg_id"] == msg_id:
            # Increment retry count and send to DLQ
            envelope = EventEnvelope(**json.loads(msg["message"]))
            if not hasattr(envelope, "retry_count"):
                envelope.retry_count = 0
            envelope.retry_count += 1
            if envelope.retry_count >= 3:
                await self.send(f"{queue_name}_dlq", envelope)
                await self.delete(queue_name, msg_id)
            else:
                # Re‑queue with updated retry count
                await self.send(queue_name, envelope)
                await self.delete(queue_name, msg_id)
