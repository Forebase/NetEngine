"""Base gateway handler interface.

The gateway is not a service; it's a role. This interface abstracts
gateway implementations (Alpine+nftables for M0, VyOS for future BGP scope).

Enables swapping implementations without changing call sites.
"""

from abc import ABC, abstractmethod


class BaseGatewayHandler(ABC):
    """Abstract base for gateway implementations.

    Used in Phase 7 (ANDs) and Phase 8 (services) for network policy.
    Implementations: nftables (M0), VyOS (future).
    """

    @abstractmethod
    async def generate_rules(self, and_name: str, profile: str, cidr: str) -> str:
        """Generate network/firewall rules for an AND.

        Args:
            and_name: AND identifier (used in rule table names)
            profile: AND profile name (residential | business | datacenter | airgapped)
            cidr: Allocated subnet CIDR for this AND

        Returns:
            Ruleset string (nftables syntax for nftables impl; vendor-specific for others)

        Raises:
            ValueError: If profile is unknown
        """

    @abstractmethod
    async def apply_rules(self, and_name: str, rules: str) -> None:
        """Write and activate rules on the gateway for a single AND.

        Args:
            and_name: AND identifier
            rules: Ruleset string produced by generate_rules

        Raises:
            RuntimeError: If writing or activating fails
        """

    @abstractmethod
    async def remove_rules(self, and_name: str) -> None:
        """Remove all rules for an AND (called on AND teardown).

        Args:
            and_name: AND identifier

        Raises:
            RuntimeError: If removal fails (table-not-found is silently ignored)
        """

    @abstractmethod
    async def reload(self) -> None:
        """Reload full gateway configuration after a container restart.

        Raises:
            RuntimeError: If reload fails
        """
