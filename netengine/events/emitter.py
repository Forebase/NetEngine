"""Shared helpers for emitting NetEngine events to PGMQ."""

from datetime import UTC, datetime
from typing import Any

from netengine.events import factory as event_factory
from netengine.events.queues import Queue, queue_for_event_type
from netengine.events.schema import EventEnvelope
from netengine.handlers.context import PhaseContext


def _failure_record(event: EventEnvelope, queue: Queue | str, exc: Exception) -> dict[str, Any]:
    """Build the structured runtime-state record for an event send failure."""
    return {
        "event_type": event.event_type,
        "queue": str(queue),
        "emitted_by": event.emitted_by,
        "exception": str(exc),
        "event_id": event.event_id,
        "correlation_id": event.correlation_id,
        "parent_event_id": event.parent_event_id,
        "failed_at": datetime.now(UTC).isoformat(),
    }


def record_event_send_failure(
    context: PhaseContext,
    event: EventEnvelope,
    queue: Queue | str,
    exc: Exception,
) -> dict[str, Any]:
    """Persist structured details about a failed event send in runtime state.

    The list is intentionally small and state-file friendly. It gives callers and
    diagnostics a durable view of recent failures instead of relying only on log
    messages.
    """
    record = _failure_record(event, queue, exc)
    failures = getattr(context.runtime_state, "event_send_failures", None)
    if failures is None:
        failures = []
        setattr(context.runtime_state, "event_send_failures", failures)
    failures.append(record)
    del failures[:-50]

    output_field = getattr(context, "phase_name", None)
    phase_output_map = {
        "substrate": "substrate_output",
        "dns": "dns_output",
        "pki": "pki_output",
        "inworld_identity": "identity_inworld_output",
        "ands": "ands_output",
        "services": "world_services_output",
        "gateway_portal": "gateway_portal_output",
    }
    if output_field in phase_output_map:
        output_field = phase_output_map[output_field]
    elif event.emitted_by == "substrate_handler":
        output_field = "substrate_output"
    elif event.emitted_by == "dns_handler":
        output_field = "dns_output"
    elif event.emitted_by == "pki_phase":
        output_field = "pki_output"
    elif event.emitted_by == "inworld_identity_handler":
        output_field = "identity_inworld_output"
    elif event.emitted_by == "ands_handler":
        output_field = "ands_output"
    elif event.emitted_by == "services_handler":
        output_field = "world_services_output"
    elif event.emitted_by == "gateway_portal_handler":
        output_field = "gateway_portal_output"

    phase_output = getattr(context.runtime_state, output_field, None) if output_field else None
    if isinstance(phase_output, dict):
        phase_output.setdefault("event_send_failures", []).append(record)

    return record


async def emit_event(
    context: PhaseContext,
    *,
    event_type: str,
    emitted_by: str,
    payload: dict[str, Any],
    queue: Queue | None = None,
) -> EventEnvelope:
    """Create, log, and best-effort enqueue an event.

    Send failures are captured on ``runtime_state.event_send_failures`` and, when
    a phase output dict already exists, under that output's
    ``event_send_failures`` key.
    """
    event = event_factory.phase_event(
        event_type=event_type,
        emitted_by=emitted_by,
        payload=payload,
        correlation_id=getattr(context.runtime_state, "correlation_id", None),
        parent_event_id=getattr(context.runtime_state, "parent_event_id", None),
    )
    context.logger.info(
        f"Event emitted: {event_type} "
        f"(event_id={event.event_id}, correlation_id={event.correlation_id})"
    )

    if context.pgmq_client is None:
        context.logger.debug("pgmq_client not available; event logged only")
        return event

    target_queue = queue or queue_for_event_type(event_type)
    try:
        await context.pgmq_client.send(target_queue, event)
        context.logger.debug(f"Event queued to pgmq: {event_type}")
    except Exception as exc:
        record = record_event_send_failure(context, event, target_queue, exc)
        context.logger.warning(f"Failed to queue event to pgmq: {record}")
    return event
