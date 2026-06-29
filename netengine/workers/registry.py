"""Background worker inventory and status helpers for operator surfaces."""

from typing import Any

from netengine.core.state import RuntimeState

PGMQ_WORKERS: tuple[tuple[str, str], ...] = (
    ("pki.cert_rotation", "PKI certificate rotation"),
    ("ands.org_admission", "AND organization admission provisioning"),
    ("services.org_admission", "World-services organization admission provisioning"),
    ("monitoring.world_health", "Monitoring world-health publisher"),
    ("drift_detection", "Phase drift detection"),
    ("dlq.services_admissions", "Services admissions DLQ replay"),
)
ALWAYS_WORKERS: tuple[tuple[str, str], ...] = (
    ("whois_server", "WHOIS registry server"),
    ("dns_updates", "Registry DNS update consumer"),
)


def expected_worker_statuses(
    state: RuntimeState, *, pgmq_enabled: bool, live: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """Build structured worker status for API/doctor without requiring in-process tasks."""
    live = live or {}
    statuses: dict[str, dict[str, Any]] = {}

    def add(name: str, description: str, enabled: bool, reason: str | None = None) -> None:
        if name in live:
            statuses[name] = {**live[name], "description": description}
            return
        statuses[name] = {
            "name": name,
            "description": description,
            "state": "registered" if enabled else "disabled",
            "disabled_reason": reason,
            "last_error": None,
            "restarts": 0,
        }

    if state.phase_completed.get("5"):
        for name, desc in ALWAYS_WORKERS:
            add(name, desc, True)
    if state.phase_completed.get("3"):
        add(PGMQ_WORKERS[0][0], PGMQ_WORKERS[0][1], pgmq_enabled, "pgmq unavailable")
    if state.phase_completed.get("7"):
        add(PGMQ_WORKERS[1][0], PGMQ_WORKERS[1][1], pgmq_enabled, "pgmq unavailable")
    if state.phase_completed.get("8"):
        for name, desc in PGMQ_WORKERS[2:4] + PGMQ_WORKERS[5:]:
            add(name, desc, pgmq_enabled, "pgmq unavailable")
    add(PGMQ_WORKERS[4][0], PGMQ_WORKERS[4][1], pgmq_enabled, "pgmq unavailable")
    return statuses
