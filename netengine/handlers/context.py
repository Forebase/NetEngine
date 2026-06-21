"""Phase execution context and runtime state."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from netengine.core.state import RuntimeState
from netengine.spec.models import NetEngineSpec


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
