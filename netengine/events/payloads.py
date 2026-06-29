"""Typed payload definitions for known NetEngine events."""

from typing import Any, Literal, TypeAlias, TypedDict


class OrgAdmittedPayload(TypedDict):
    org_name: str
    capabilities: list[str]
    and_profile: str


class OrgUpdatedPayload(TypedDict):
    org_name: str
    capabilities: list[str]
    and_profile: str


class OrgRemovedPayload(TypedDict):
    org_name: str


class DomainRegisteredPayload(TypedDict):
    domain: str
    org: str
    ns: list[str]


class DriftDetectedPayload(TypedDict):
    phase: int
    handler: str
    detected_at: str


class DriftLoopErrorPayload(TypedDict):
    error: str
    error_at: str


class DriftSelfHealedPayload(TypedDict):
    phase: int
    healed_at: str


class DriftSelfHealFailedPayload(TypedDict):
    phase: int
    error: str
    failed_at: str


class GenericEventPayload(TypedDict, total=False):
    """Fallback payload for explicitly unknown or extension events."""

    __extension_event__: bool
    data: dict[str, Any]


KnownEventType: TypeAlias = Literal[
    "org.admitted",
    "org.updated",
    "org.removed",
    "domain.registered",
    "drift.detected",
    "drift.loop_error",
    "drift.self_healed",
    "drift.self_heal_failed",
]

PhaseEventType: TypeAlias = Literal[
    "substrate.initialized",
    "dns.zones_ready",
    "pki.ready",
    "inworld_identity.ready",
]

EventType: TypeAlias = KnownEventType | PhaseEventType

KnownEventPayload: TypeAlias = (
    OrgAdmittedPayload
    | OrgUpdatedPayload
    | OrgRemovedPayload
    | DomainRegisteredPayload
    | DriftDetectedPayload
    | DriftLoopErrorPayload
    | DriftSelfHealedPayload
    | DriftSelfHealFailedPayload
    | dict[str, Any]
)
