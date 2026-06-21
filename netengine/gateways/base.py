"""Base gateway handler interface.

The gateway is not a service; it's a role. This interface abstracts
gateway implementations (Alpine+nftables for M0, VyOS for future BGP scope).

Enables swapping implementations without changing call sites.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from netengine.handlers.context import PhaseContext


@dataclass
class Rule:
    """Generic rule representation (implementation-specific serialization in handlers)."""

    rule_id: str
    priority: int
    content: dict[str, Any]


class BaseGatewayHandler(ABC):
    """Abstract base for gateway implementations.

    Used in Phase 7 (ANDs) and Phase 8 (services) for network policy.
    Implementations: nftables (M0), VyOS (future).
    """

    @abstractmethod
    async def generate_rules(self, context: PhaseContext) -> list[Rule]:
        """Generate network/firewall rules from AND profiles or service definitions.

        Args:
            context: PhaseContext with spec and state

        Returns:
            List of rules to be applied

        Raises:
            Exception: If rule generation fails
        """
        pass

    @abstractmethod
    async def apply_rules(self, context: PhaseContext, rules: list[Rule]) -> None:
        """Apply rules to the gateway atomically.

        Args:
            context: PhaseContext with spec and state
            rules: Rules to apply

        Raises:
            Exception: If apply fails; gateway state is undefined
        """
        pass

    @abstractmethod
    async def remove_rules(self, context: PhaseContext, rule_ids: list[str]) -> None:
        """Remove specific rules by ID.

        Args:
            context: PhaseContext with spec and state
            rule_ids: Rule IDs to remove

        Raises:
            Exception: If removal fails
        """
        pass

    @abstractmethod
    async def reload(self, context: PhaseContext) -> None:
        """Reload full gateway configuration (rolling restart, etc.).

        Used when gateway implementation (not rules) changes, or after
        gateway container restart.

        Args:
            context: PhaseContext with spec and state

        Raises:
            Exception: If reload fails
        """
        pass
