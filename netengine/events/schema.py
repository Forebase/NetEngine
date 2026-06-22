"""Event envelope schema for pgmq inter-handler communication.

Locked at M0. All inter-handler events (M4+) must use this schema.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


@dataclass
class EventEnvelope:
    """Message envelope for pgmq inter-handler events.

    All handlers emit events with this structure. Enables:
    - Causality tracing (correlation_id + parent_event_id)
    - Event graph observability
    - DLQ retry logic
    - Handler decoupling via async event queuing

    Locked at M0 to prevent retrofitting tracing/retry across all handlers later.
    """

    event_id: str
    """Unique event identifier (UUID v4)."""

    correlation_id: str
    """Root trace ID. Same for all events in a causality chain.
    Equal to event_id for root events.
    """

    event_type: str
    """Event type identifier.
    Format: {phase}.{action} (e.g., "dns.zone_update_required", "pki.cert_issued")
    """

    emitted_by: str
    """Handler or service that emitted this event.
    Format: {handler_name} (e.g., "dns_handler", "pki_handler")
    """

    emitted_at: datetime
    """ISO 8601 timestamp when event was emitted."""

    payload: dict[str, Any]
    """Handler-specific event data. Schema varies by event_type."""

    parent_event_id: Optional[str] = None
    """Direct parent event in the causality chain.
    None for root events.
    """

    retry_count: int = 0
    """Number of times this message has been retried.
    Incremented by event queue. After N retries, moved to DLQ.
    """

    @staticmethod
    def create(
        event_type: str,
        emitted_by: str,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ) -> "EventEnvelope":
        """Create a new event envelope.

        Args:
            event_type: Type of event
            emitted_by: Handler name that created this event
            payload: Event-specific data
            correlation_id: Trace ID (auto-generated if None)
            parent_event_id: Parent event ID (None for root events)

        Returns:
            New EventEnvelope instance
        """
        event_id = str(uuid4())
        correlation_id = correlation_id or event_id

        return EventEnvelope(
            event_id=event_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            event_type=event_type,
            emitted_by=emitted_by,
            emitted_at=datetime.now(timezone.utc),
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize envelope to dict (for pgmq storage)."""
        data = asdict(self)
        data["emitted_at"] = self.emitted_at.isoformat()
        return data
