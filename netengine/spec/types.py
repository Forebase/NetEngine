"""Type definitions, enums, and constants for NetEngine specs."""

from enum import Enum


class Lifecycle(str, Enum):
    """World lifecycle mode."""

    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"


class Orchestrator(str, Enum):
    """Container orchestration platform."""

    SWARM = "swarm"
    K3S = "k3s"


class GatewayRealInternetMode(str, Enum):
    """How the world relates to the real internet."""

    ISOLATED = "isolated"
    SHADOWED = "shadowed"
    MIRRORED = "mirrored"
    EXPOSED = "exposed"
    CUSTOM = "custom"


class GatewayCrossWorldMode(str, Enum):
    """How this world relates to other NetEngine worlds."""

    NONE = "none"
    PEERED = "peered"
    FEDERATED = "federated"


class ANDProfile(str, Enum):
    """Network profile for Administrative Network Domains."""

    RESIDENTIAL = "residential"
    BUSINESS = "business"
    DATACENTER = "datacenter"
    AIRGAPPED = "airgapped"


class SerialPolicy(str, Enum):
    """DNSSEC serial number policy."""

    FIXED = "fixed"
    TIMESTAMP = "timestamp"


class OperatorRole(str, Enum):
    """Operator role in the world."""

    SUPERADMIN = "superadmin"
    READONLY = "readonly"


class AppScope(str, Enum):
    """Scope of org app availability."""

    DEV_ONLY = "dev_only"
    EPHEMERAL_ONLY = "ephemeral_only"
    ALL = "all"
