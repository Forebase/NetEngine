"""PGMQ (PostgreSQL Message Queue) connector."""

import json
from typing import Any, Dict, Optional

from loguru import logger

from netengine.connectors.base import Connector
from netengine.connectors.postgres_connector import PostgresConnector
from netengine.events.schema import EventEnvelope


class PGMQConnector(Connector):
    """Manages pgmq queue operations via PostgreSQL RPC."""

    def __init__(self, postgres: PostgresConnector) -> None:
        self._postgres = postgres

    async def connect(self) -> None:
        """Ensure PostgreSQL connector is connected."""
        if not self._postgres._pool:
            await self._postgres.connect()
        logger.info("PGMQ connector initialized")

    async def disconnect(self) -> None:
        """PGMQ uses shared PostgreSQL connection; no separate cleanup."""
        pass

    async def health(self) -> bool:
        """Check PGMQ via database health."""
        return await self._postgres.health()

    async def send(self, queue_name: str, message: EventEnvelope) -> None:
        """Send message to queue."""
        msg_json = json.dumps(
            {
                "event_id": message.event_id,
                "correlation_id": message.correlation_id,
                "event_type": message.event_type,
                "emitted_by": message.emitted_by,
                "emitted_at": message.emitted_at.isoformat(),
                "payload": message.payload,
                "parent_event_id": message.parent_event_id,
                "retry_count": message.retry_count,
            }
        )
        await self._postgres.rpc("pgmq_send", {"queue_name": queue_name, "msg": msg_json})

    async def receive(
        self, queue_name: str, timeout: int = 5
    ) -> Optional[Dict[str, Any]]:
        """Receive message from queue with timeout."""
        result = await self._postgres.rpc(
            "pgmq_pop", {"queue_name": queue_name, "limit": 1, "vt": timeout}
        )
        if not result or len(result) == 0:
            return None
        return result[0]

    async def delete(self, queue_name: str, msg_id: int) -> None:
        """Delete message from queue."""
        await self._postgres.rpc(
            "pgmq_delete", {"queue_name": queue_name, "msg_id": msg_id}
        )

    async def read_by_id(self, queue_name: str, msg_id: int) -> Optional[Dict[str, Any]]:
        """Read message without consuming it."""
        result = await self._postgres.rpc(
            "pgmq_read_by_id", {"queue_name": queue_name, "msg_id": msg_id}
        )
        return result[0] if result else None

    async def archive_to_dlq(self, queue_name: str, msg_id: int, reason: str) -> None:
        """Move message to dead-letter queue after max retries."""
        dlq_name = f"{queue_name}_dlq"
        msg = await self.read_by_id(queue_name, msg_id)
        if msg:
            msg_data = json.loads(msg["message"])
            msg_data["archive_reason"] = reason
            msg_data["archived_at"] = str(__import__("datetime").datetime.utcnow())
            await self.send(dlq_name, EventEnvelope(**msg_data))
            await self.delete(queue_name, msg_id)
