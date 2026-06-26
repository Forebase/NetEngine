"""Base abstraction for external system connectors."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

TConfig = TypeVar("TConfig")


class Connector(ABC, Generic[TConfig]):
    """Typed wrapper for external system access (Docker, DB, APIs, etc.)."""

    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection, auth, pooling. Called once at startup."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connections, release resources. Called on shutdown."""
        pass

    @abstractmethod
    async def health(self) -> bool:
        """Quick health check. Used by diagnostics and monitoring."""
        pass
