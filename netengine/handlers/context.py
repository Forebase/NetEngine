"""Phase execution context and runtime state."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from netengine.core.state import RuntimeState
from netengine.spec.models import NetEngineSpec

if TYPE_CHECKING:
    from netengine.core.consumer_supervisor import ConsumerSupervisor

# Default directory for CoreDNS Corefile and zone files.
# Overridden by NETENGINE_ZONE_DIR env var.
DEFAULT_ZONE_DIR = str(Path.cwd() / "data" / "coredns")


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
    consumer_supervisor: Optional["ConsumerSupervisor"] = None

    # Phase-specific config
    phase_name: Optional[str] = None
    phase_config: Optional[dict[str, Any]] = None

    # When True, handlers skip real infrastructure calls (Docker, DNS queries, etc.)
    # and return stub outputs. Set via NETENGINE_MOCK=true env var.
    mock_mode: bool = field(
        default_factory=lambda: os.environ.get("NETENGINE_MOCK", "").lower() in ("1", "true", "yes")
    )

    # Directory where the DNS handler writes Corefile + zone files.
    # CoreDNS container bind-mounts this directory to /etc/coredns.
    zone_dir: str = field(
        default_factory=lambda: os.environ.get("NETENGINE_ZONE_DIR", DEFAULT_ZONE_DIR)
    )
