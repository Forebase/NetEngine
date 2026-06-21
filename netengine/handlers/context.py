"""Phase execution context and runtime state."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from netengine.spec.models import NetEngineSpec


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

    # Error tracking
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


@dataclass
class PhaseContext:
    """Dependency injection container for phase handlers.

    All handlers receive this object; use field values to access
    spec, state, logger, and service clients.
    """

    spec: NetEngineSpec
    runtime_state: RuntimeState
    logger: logging.Logger

    # Service clients (stubbed in M0, populated in M1+)
    docker_client: Any = None
    kubernetes_client: Any = None
    supabase_client: Any = None
    pgmq_client: Any = None

    # Phase-specific config
    phase_name: Optional[str] = None
    phase_config: Optional[dict[str, Any]] = None
