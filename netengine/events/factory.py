"""Typed factory functions for creating event envelopes."""

from typing import Any, Mapping

from netengine.events.payloads import (
    DomainRegisteredPayload,
    DriftDetectedPayload,
    DriftLoopErrorPayload,
    DriftSelfHealedPayload,
    DriftSelfHealFailedPayload,
    GenericEventPayload,
    OrgAdmittedPayload,
    OrgRemovedPayload,
    OrgUpdatedPayload,
)
from netengine.events.schema import EventEnvelope


def _create(
    *,
    event_type: str,
    emitted_by: str,
    payload: Mapping[str, Any],
    correlation_id: str | None = None,
    parent_event_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type,
        emitted_by=emitted_by,
        payload=dict(payload),
        correlation_id=correlation_id,
        parent_event_id=parent_event_id,
    )


def org_admitted(
    *,
    org_name: str,
    capabilities: list[str],
    and_profile: str,
    correlation_id: str | None = None,
    parent_event_id: str | None = None,
) -> EventEnvelope:
    payload: OrgAdmittedPayload = {
        "org_name": org_name,
        "capabilities": capabilities,
        "and_profile": and_profile,
    }
    return _create(
        event_type="org.admitted",
        emitted_by="world_registry_handler",
        payload=payload,
        correlation_id=correlation_id,
        parent_event_id=parent_event_id,
    )


def org_updated(*, org_name: str, capabilities: list[str], and_profile: str) -> EventEnvelope:
    payload: OrgUpdatedPayload = {
        "org_name": org_name,
        "capabilities": capabilities,
        "and_profile": and_profile,
    }
    return _create(event_type="org.updated", emitted_by="world_registry_handler", payload=payload)


def org_removed(*, org_name: str) -> EventEnvelope:
    payload: OrgRemovedPayload = {"org_name": org_name}
    return _create(event_type="org.removed", emitted_by="world_registry_handler", payload=payload)


def domain_registered(*, domain: str, org_name: str, ns_records: list[str]) -> EventEnvelope:
    payload: DomainRegisteredPayload = {"domain": domain, "org": org_name, "ns": ns_records}
    return _create(
        event_type="domain.registered", emitted_by="domain_registry_handler", payload=payload
    )


def drift_detected(*, phase: int, handler: str, detected_at: str) -> EventEnvelope:
    payload: DriftDetectedPayload = {"phase": phase, "handler": handler, "detected_at": detected_at}
    return _create(event_type="drift.detected", emitted_by="drift_controller", payload=payload)


def drift_loop_error(*, error: str, error_at: str) -> EventEnvelope:
    payload: DriftLoopErrorPayload = {"error": error, "error_at": error_at}
    return _create(event_type="drift.loop_error", emitted_by="drift_controller", payload=payload)


def drift_self_healed(*, phase: int, healed_at: str) -> EventEnvelope:
    payload: DriftSelfHealedPayload = {"phase": phase, "healed_at": healed_at}
    return _create(event_type="drift.self_healed", emitted_by="drift_controller", payload=payload)


def drift_self_heal_failed(*, phase: int, error: str, failed_at: str) -> EventEnvelope:
    payload: DriftSelfHealFailedPayload = {"phase": phase, "error": error, "failed_at": failed_at}
    return _create(
        event_type="drift.self_heal_failed", emitted_by="drift_controller", payload=payload
    )


def extension_event(*, event_type: str, emitted_by: str, data: dict[str, Any]) -> EventEnvelope:
    payload: GenericEventPayload = {"__extension_event__": True, "data": data}
    return _create(event_type=event_type, emitted_by=emitted_by, payload=payload)


def phase_event(
    *,
    event_type: str,
    emitted_by: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    parent_event_id: str | None = None,
) -> EventEnvelope:
    """Create a typed phase event emitted through the shared emitter.

    Phase handlers still have heterogeneous payloads; routing through this factory
    centralizes envelope creation while dedicated payload types are added
    incrementally.
    """
    return _create(
        event_type=event_type,
        emitted_by=emitted_by,
        payload=payload,
        correlation_id=correlation_id,
        parent_event_id=parent_event_id,
    )
