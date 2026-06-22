import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "netengines_state.json"


def get_state_file() -> Path:
    """Return the runtime state file path for the current environment."""
    return Path(os.environ.get("NETENGINE_STATE_FILE", DEFAULT_STATE_FILE))


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
        state_file = get_state_file()
        if state_file.exists():
            with open(state_file, "r") as f:
                data = json.load(f)
            # datetime fields are stored as ISO strings
            for dt_field in ("started_at", "completed_at", "last_error_at"):
                if data.get(dt_field):
                    data[dt_field] = datetime.fromisoformat(data[dt_field])
            state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            state._discard_completion_flags_without_outputs()
            return state
        return cls()

    def _discard_completion_flags_without_outputs(self) -> None:
        """Ignore completion flags that do not have their matching phase output."""
        phase_outputs = {
            "0": ("substrate_output",),
            "1": ("dns_output",),
            "2": ("dns_output",),
            "3": ("pki_bootstrapped",),
            "4": ("identity_platform_output",),
            "5": ("world_registry_output", "domain_registry_output"),
            "6": ("identity_inworld_output",),
            "7": ("ands_output",),
            "8": ("world_services_output",),
            "9": ("org_apps_output",),
        }
        for phase, output_fields in phase_outputs.items():
            if self.phase_completed.get(phase) is True and not all(
                getattr(self, output_field, None) for output_field in output_fields
            ):
                self.phase_completed.pop(phase, None)

    def save(self) -> None:
        self._discard_completion_flags_without_outputs()
        data = asdict(self)
        # Serialize datetime fields to ISO strings
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
        state_file = get_state_file()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(data, f, indent=2)

    def sync_to_supabase(self) -> None:
        """Write current state snapshot to Supabase runtime_state table (audit log)."""
        try:
            from netengine.core.supabase_client import get_supabase

            supabase = get_supabase()
            data = asdict(self)
            for k, v in data.items():
                if isinstance(v, datetime):
                    data[k] = v.isoformat()
            supabase.table("runtime_state").upsert(
                {"key": "current", "value": data, "updated_at": datetime.utcnow().isoformat()}
            ).execute()
        except Exception as exc:
            logger.debug(f"Supabase state sync skipped: {exc}")
