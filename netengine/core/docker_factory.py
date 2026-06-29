"""Docker client factory for NetEngine orchestration.

Isolates Docker SDK initialisation so Orchestrator stays focused on phase
sequencing, and so the initialisation path is testable in isolation.
"""

import os
from typing import Any, Optional

from netengine.logging import get_logger

logger = get_logger(__name__)


def create_docker_client() -> tuple[Optional[Any], bool]:
    """Initialise a Docker client, falling back to mock mode on failure.

    Returns:
        (client, effective_mock) where client is None when mock_mode is True.
    """
    mock_env = os.environ.get("NETENGINE_MOCK", "").lower() in ("1", "true", "yes")
    if mock_env:
        return None, True

    try:
        from netengine.handlers.docker_handler import DockerHandler

        client: Any = DockerHandler()
        return client, False
    except Exception as exc:
        logger.warning(f"Docker unavailable, falling back to mock mode: {exc}")
        return None, True
