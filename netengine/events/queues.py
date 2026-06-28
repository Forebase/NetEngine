"""Queue name registry for pgmq inter-handler event queues.

All queue names must be defined here. Handlers and consumers import from this
module instead of using string literals, so renames and additions are a
single-point change.
"""

from enum import StrEnum


class Queue(StrEnum):
    DNS_UPDATES = "dns_updates"
    OIDC_PROVISIONING = "oidc_provisioning"
    AND_PROVISIONING = "and_provisioning"
    INWORLD_ADMISSIONS = "inworld_admissions"
    SERVICES_ADMISSIONS = "services_admissions"
    AND_ADMISSIONS = "and_admissions"
    PKI_CERT_ROTATION_EVENTS = "pki_cert_rotation_events"
    DRIFT_EVENTS = "drift_events"
    WORLD_HEALTH = "world_health"
    GATEWAY_PORTAL_EVENTS = "gateway_portal_events"

    # Dead-letter queues (derived from primary names)
    DNS_UPDATES_DLQ = "dns_updates_dlq"
    OIDC_PROVISIONING_DLQ = "oidc_provisioning_dlq"
    AND_PROVISIONING_DLQ = "and_provisioning_dlq"
    INWORLD_ADMISSIONS_DLQ = "inworld_admissions_dlq"
    SERVICES_ADMISSIONS_DLQ = "services_admissions_dlq"
    AND_ADMISSIONS_DLQ = "and_admissions_dlq"
    PKI_CERT_ROTATION_EVENTS_DLQ = "pki_cert_rotation_events_dlq"
    DRIFT_EVENTS_DLQ = "drift_events_dlq"
    WORLD_HEALTH_DLQ = "world_health_dlq"
    GATEWAY_PORTAL_EVENTS_DLQ = "gateway_portal_events_dlq"


# Primary queues only — used for metrics/introspection endpoints
PRIMARY_QUEUES: tuple[Queue, ...] = (
    Queue.DNS_UPDATES,
    Queue.OIDC_PROVISIONING,
    Queue.AND_PROVISIONING,
    Queue.INWORLD_ADMISSIONS,
    Queue.SERVICES_ADMISSIONS,
    Queue.AND_ADMISSIONS,
    Queue.PKI_CERT_ROTATION_EVENTS,
    Queue.DRIFT_EVENTS,
    Queue.WORLD_HEALTH,
    Queue.GATEWAY_PORTAL_EVENTS,
)
