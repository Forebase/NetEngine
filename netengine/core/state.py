import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
from pathlib import Path

@dataclass
class RuntimeState:
    """Mutable runtime state, saved to a local JSON file."""
    phase_completed: Dict[str, bool] = field(default_factory=dict)
    # Phase 0-2 (substrate, DNS) outputs
    gateway_container_id: Optional[str] = None
    dns_root_container_id: Optional[str] = None
    # Phase 3 (PKI) outputs
    ca_cert_pem: Optional[str] = None
    step_ca_container_id: Optional[str] = None
    pki_bootstrapped: bool = False
    # Phase 4 (platform identity) outputs – will be added in M3
    keycloak_platform_container_id: Optional[str] = None
    platform_realm_id: Optional[str] = None
    admin_user_id: Optional[str] = None
    # General
    world_spec: Optional[Dict[str, Any]] = None
    bootstrap_admin_password: Optional[str] = None

    #
    STATE_FILE = Path(os.environ.get("NETENGINES_STATE_FILE", "netengines_state.json"))

    @classmethod
    def load(cls) -> "RuntimeState":
        if cls.STATE_FILE.exists():
            with open(cls.STATE_FILE, "r") as f:
                data = json.load(f)
            return cls(**data)
        return cls()

    def save(self) -> None:
        with open(self.STATE_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)