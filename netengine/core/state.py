from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from uuid import uuid4


@dataclass
class RuntimeState:
    """Mutable state tracking across phase execution.

    All fields are initially None/empty; populated by phase handlers.
    Immutable spec is passed separately via PhaseContext.
    """

    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    parent_event_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Phase output stubs (populated by handlers M1+)
    substrate_output: Optional[dict[str, Any]] = None
    dns_output: Optional[dict[str, Any]] = None
    pki_output: Optional[dict[str, Any]] = None
    identity_platform_output: Optional[dict[str, Any]] = None
    world_registry_output: Optional[dict[str, Any]] = None
    domain_registry_output: Optional[dict[str, Any]] = None
    identity_inworld_output: Optional[dict[str, Any]] = None
    ands_output: Optional[dict[str, Any]] = None
    world_services_output: Optional[dict[str, Any]] = None
    org_apps_output: Optional[dict[str, Any]] = None
    gateway_portal_output: Optional[dict[str, Any]] = None
    operator_output: Optional[dict[str, Any]] = None

    #
    ca_cert_pem: Optional[str] = None
    ca_key_pem: Optional[str] = None  # store only if needed for later
    step_ca_container_id: Optional[str] = None

    # Error tracking
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
