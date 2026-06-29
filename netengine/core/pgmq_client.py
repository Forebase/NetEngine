import json
from datetime import datetime
from importlib import import_module
from typing import Any, NotRequired, Protocol, TypedDict, cast

from netengine.events.queues import Queue, dlq_for
from netengine.events.schema import EventEnvelope

MAX_RETRIES = 3


class PGMQMessage(TypedDict):
    """Typed shape returned by PGMQ pop/read operations."""

    msg_id: int
    message: str
    read_ct: NotRequired[int]
    enqueued_at: NotRequired[str]
    vt: NotRequired[str]


class _QueryResult(Protocol):
    data: object


class _ExecutableQuery(Protocol):
    async def execute(self) -> _QueryResult: ...


class _TableQuery(_ExecutableQuery, Protocol):
    def select(self, cols: str = "*") -> "_TableQuery": ...
    def insert(self, data: dict[str, Any]) -> "_TableQuery": ...
    def upsert(self, data: dict[str, Any]) -> "_TableQuery": ...
    def update(self, data: dict[str, Any]) -> "_TableQuery": ...
    def delete(self) -> "_TableQuery": ...
    def eq(self, col: str, val: Any) -> "_TableQuery": ...
    def limit(self, n: int) -> "_TableQuery": ...


class _DatabaseClient(Protocol):
    def rpc(self, func_name: str, params: dict[str, Any]) -> _ExecutableQuery: ...
    def table(self, name: str) -> _TableQuery: ...


class _GetDb(Protocol):
    async def __call__(self) -> _DatabaseClient: ...


class PGMQClient:
    def __init__(self) -> None:
        self._db: _DatabaseClient | None = None

    async def _get_db(self) -> _DatabaseClient:
        if self._db is None:
            module = import_module("netengine.core.supabase_client")
            get_db = cast(_GetDb, module.get_db)
            self._db = await get_db()
        return self._db

    async def send(self, queue_name: Queue, event: EventEnvelope) -> int:
        """Enqueue an event; returns message ID."""
        db = await self._get_db()
        payload = self._encode_envelope(event)
        result = await db.rpc("pgmq_send", {"queue_name": queue_name, "message": payload}).execute()
        return self._message_id_from_result(result.data, queue_name)

    async def receive(self, queue_name: Queue, timeout: int = 5) -> PGMQMessage | None:
        """Pop a message from the queue."""
        db = await self._get_db()
        result = await db.rpc("pgmq_pop", {"queue_name": queue_name, "timeout": timeout}).execute()
        return self._message_from_result(result.data, "pgmq_pop", queue_name)

    async def delete(self, queue_name: Queue, msg_id: int) -> None:
        """Acknowledge and delete a processed message."""
        db = await self._get_db()
        await db.rpc("pgmq_delete", {"queue_name": queue_name, "msg_id": msg_id}).execute()

    async def read_by_id(self, queue_name: Queue, msg_id: int) -> PGMQMessage | None:
        """Read a specific message by ID without consuming it."""
        db = await self._get_db()
        result = await db.rpc(
            "pgmq_read_by_id", {"queue_name": queue_name, "msg_id": msg_id}
        ).execute()
        return self._message_from_result(result.data, "pgmq_read_by_id", queue_name)

    async def archive_to_dlq(self, queue_name: Queue, msg_id: int, reason: str) -> None:
        """Re-queue with incremented retry count, or move to DLQ after MAX_RETRIES."""
        msg = await self.read_by_id(queue_name, msg_id)
        if not msg:
            return

        envelope = self._decode_envelope(msg)
        await self.delete(queue_name, msg_id)

        if envelope.retry_count + 1 >= MAX_RETRIES:
            dlq_envelope = self._retry_envelope(
                envelope, payload={**envelope.payload, "dlq_reason": reason}
            )
            await self.send(dlq_for(queue_name), dlq_envelope)
        else:
            requeue_envelope = self._retry_envelope(envelope, payload=envelope.payload)
            await self.send(queue_name, requeue_envelope)

    @staticmethod
    def _encode_envelope(event: EventEnvelope) -> str:
        return json.dumps(event.to_dict())

    @staticmethod
    def _decode_envelope(msg: PGMQMessage) -> EventEnvelope:
        payload = json.loads(msg["message"])
        if not isinstance(payload, dict):
            raise RuntimeError(f"PGMQ message {msg['msg_id']} did not contain a JSON object")
        return PGMQClient._envelope_from_payload(cast(dict[str, Any], payload), msg["msg_id"])

    @staticmethod
    def _envelope_from_payload(payload: dict[str, Any], msg_id: int) -> EventEnvelope:
        emitted_at = payload.get("emitted_at")
        if isinstance(emitted_at, str):
            payload = {**payload, "emitted_at": datetime.fromisoformat(emitted_at)}
        elif not isinstance(emitted_at, datetime):
            raise RuntimeError(
                f"PGMQ message {msg_id} did not contain a valid emitted_at timestamp"
            )
        return EventEnvelope(**payload)

    @staticmethod
    def _retry_envelope(envelope: EventEnvelope, payload: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            event_id=envelope.event_id,
            correlation_id=envelope.correlation_id,
            event_type=envelope.event_type,
            emitted_by=envelope.emitted_by,
            emitted_at=envelope.emitted_at,
            payload=payload,
            parent_event_id=envelope.parent_event_id,
            retry_count=envelope.retry_count + 1,
        )

    @staticmethod
    def _message_id_from_result(data: object, queue_name: Queue) -> int:
        first = PGMQClient._first_result_row(data, "pgmq_send", queue_name)
        if isinstance(first, int):
            return first
        if isinstance(first, dict):
            value = first.get("msg_id")
            if isinstance(value, int):
                return value
        raise RuntimeError(f"pgmq_send returned invalid message ID for queue '{queue_name}'")

    @staticmethod
    def _message_from_result(data: object, operation: str, queue_name: Queue) -> PGMQMessage | None:
        first = PGMQClient._first_result_row(data, operation, queue_name, required=False)
        if first is None:
            return None
        if not isinstance(first, dict):
            raise RuntimeError(f"{operation} returned a non-object row for queue '{queue_name}'")

        msg_id = first.get("msg_id")
        message = first.get("message")
        if not isinstance(msg_id, int):
            raise RuntimeError(
                f"{operation} returned a row without integer msg_id for queue '{queue_name}'"
            )
        if isinstance(message, dict):
            message = json.dumps(message)
        if not isinstance(message, str):
            raise RuntimeError(
                f"{operation} returned a row without string message for queue '{queue_name}'"
            )

        row: PGMQMessage = {"msg_id": msg_id, "message": message}
        read_ct = first.get("read_ct")
        if isinstance(read_ct, int):
            row["read_ct"] = read_ct
        for key in ("enqueued_at", "vt"):
            value = first.get(key)
            if isinstance(value, str):
                row[key] = value
        return row

    @staticmethod
    def _first_result_row(
        data: object, operation: str, queue_name: Queue, *, required: bool = True
    ) -> object | None:
        if data is None or data == []:
            if required:
                raise RuntimeError(f"{operation} returned no data for queue '{queue_name}'")
            return None
        if not isinstance(data, list):
            raise RuntimeError(f"{operation} returned non-list data for queue '{queue_name}'")
        if not data:
            if required:
                raise RuntimeError(f"{operation} returned no data for queue '{queue_name}'")
            return None
        return cast(object, data[0])
