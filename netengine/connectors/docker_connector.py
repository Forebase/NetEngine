"""Docker SDK connector with async-friendly wrapper."""

import asyncio
from typing import Any, Optional

import docker
from docker.errors import DockerException
from loguru import logger

from netengine.connectors.base import Connector


class DockerConnector(Connector[None]):
    """Manages Docker client connection and wraps sync calls for async context."""

    def __init__(self) -> None:
        self._client: Optional[docker.DockerClient] = None
        self._connected = False

    async def connect(self) -> None:
        """Initialize Docker client from environment."""
        try:
            loop = asyncio.get_running_loop()
            self._client = await loop.run_in_executor(None, docker.from_env)
            # Test connection
            await self.health()
            self._connected = True
            logger.info("Docker connector connected")
        except DockerException as e:
            logger.error(f"Failed to connect to Docker: {e}")
            raise

    async def disconnect(self) -> None:
        """Close Docker client connection."""
        if self._client:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._client.close)
            self._client = None
            self._connected = False
            logger.info("Docker connector disconnected")

    async def health(self) -> bool:
        """Check Docker daemon is responding."""
        if not self._client:
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._client.ping)
            return True
        except Exception as e:
            logger.warning(f"Docker health check failed: {e}")
            return False

    @property
    def client(self) -> docker.DockerClient:
        """Get underlying Docker client (for sync operations in executor)."""
        if not self._client:
            raise RuntimeError("Docker connector not connected. Call connect() first.")
        return self._client

    def is_connected(self) -> bool:
        """Check if connected to Docker daemon."""
        return self._connected

    async def run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking Docker operation in executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    async def run_sync_kw(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking Docker operation with kwargs in executor (slower)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
