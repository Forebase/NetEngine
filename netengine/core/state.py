import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

STATE_FILE = Path(os.environ.get("NETENGINES_STATE_FILE", "netengines_state.json"))


@dataclass
class RuntimeState:
    """Mutable runtime state, persisted to a local JSON file between phases."""

    # Execution trace
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None

    # Phase completion tracking
    phase_completed: Dict[str, bool] = field(default_factory=dict)

    # Phase outputs (one dict per phase, populated on completion)
    substrate_output: Optional[Dict[str, Any]] = None
    dns_output: Optional[Dict[str, Any]] = None
    pki_output: Optional[Dict[str, Any]] = None
    identity_platform_output: Optional[Dict[str, Any]] = None
    world_registry_output: Optional[Dict[str, Any]] = None
    domain_registry_output: Optional[Dict[str, Any]] = None
    identity_inworld_output: Optional[Dict[str, Any]] = None
    ands_output: Optional[Dict[str, Any]] = None
    world_services_output: Optional[Dict[str, Any]] = None
    org_apps_output: Optional[Dict[str, Any]] = None

    # Legacy container ID fields (kept for backward compat with existing handlers)
    gateway_container_id: Optional[str] = None
    dns_root_container_id: Optional[str] = None
    ca_cert_pem: Optional[str] = None
    step_ca_container_id: Optional[str] = None
    pki_bootstrapped: bool = False
    keycloak_platform_container_id: Optional[str] = None
    platform_realm_id: Optional[str] = None
    admin_user_id: Optional[str] = None
    inworld_keycloak_container_id: Optional[str] = None
    inworld_admin_password: Optional[str] = None
    world_spec: Optional[Dict[str, Any]] = None
    bootstrap_admin_password: Optional[str] = None

    @classmethod
    def load(cls) -> "RuntimeState":
        if STATE_FILE.exists():
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            # datetime fields are stored as ISO strings
            for dt_field in ("started_at", "completed_at", "last_error_at"):
                if data.get(dt_field):
                    data[dt_field] = datetime.fromisoformat(data[dt_field])
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self) -> None:
        data = asdict(self)
        # Serialize datetime fields to ISO strings
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
