"""Foundational authority primitives for NetEngine specifications."""

from enum import Enum
from pydantic import Field, model_validator

from netengine.spec.models import CrossWorldPeer, ServiceMirror, SpecModel
from netengine.spec.types import GatewayCrossWorldMode, GatewayRealInternetMode


class AuthorityKind(str, Enum):
    """Kinds of authority recognized by NetEngine specs."""

    WORLD_ROOT = "world_root"
    ROOT_NAMING = "root_naming"
    NUMBERING = "numbering"
    DOMAIN_REGISTRY = "domain_registry"
    REGISTRAR = "registrar"
    TRUST = "trust"
    PLATFORM_IDENTITY = "platform_identity"
    INWORLD_IDENTITY = "inworld_identity"
    TRANSIT = "transit"
    MAIL = "mail"
    SERVICE_CATALOG = "service_catalog"


class AuthorityScope(str, Enum):
    """Scope within which an authority is valid."""

    WORLD = "world"
    PLATFORM = "platform"
    INWORLD = "inworld"
    ORG = "org"
    BOUNDARY = "boundary"
    PEER = "peer"
    EXTERNAL = "external"


class AuthoritySource(str, Enum):
    """Origin for an authority definition."""

    LOCAL = "local"
    MIRRORED = "mirrored"
    IMPORTED_PEER = "imported_peer"
    EXTERNAL = "external"


class Authority(SpecModel):
    """Foundational model describing who controls a spec authority surface."""

    id: str = Field(...)
    kind: AuthorityKind = Field(...)
    scope: AuthorityScope = Field(...)
    operator: str = Field(...)
    controls: list[str] = Field(...)
    description: str | None = Field(default=None)
    source: AuthoritySource = Field(default=AuthoritySource.LOCAL)


class BoundaryPolicy(SpecModel):
    """Authority-layer policy for traffic and trust at the world boundary."""

    real_internet: GatewayRealInternetMode = Field(default=GatewayRealInternetMode.ISOLATED)
    cross_world: GatewayCrossWorldMode = Field(default=GatewayCrossWorldMode.NONE)
    service_mirrors: list[ServiceMirror] = Field(default_factory=list)
    upstream_resolver_enabled: bool = Field(default=False)
    upstream_resolver_ip: str | None = Field(default=None)
    peers: list[CrossWorldPeer] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_boundary_policy(self) -> "BoundaryPolicy":
        """Validate boundary posture combinations and required trust metadata."""
        if self.real_internet == GatewayRealInternetMode.MIRRORED and not self.service_mirrors:
            raise ValueError("mirrored real-internet posture requires at least one service mirror")

        if self.real_internet != GatewayRealInternetMode.MIRRORED and self.service_mirrors:
            raise ValueError("service mirrors require real_internet to be 'mirrored'")

        if (
            self.cross_world in {GatewayCrossWorldMode.PEERED, GatewayCrossWorldMode.FEDERATED}
            and not self.peers
        ):
            raise ValueError("cross-world peered or federated mode requires peers")

        if self.cross_world == GatewayCrossWorldMode.FEDERATED:
            peers_missing_trust = [
                peer.name
                for peer in self.peers
                if not (peer.trust_bundle or peer.trust_anchor_cert)
            ]
            if peers_missing_trust:
                missing = ", ".join(peers_missing_trust)
                raise ValueError(
                    "federated peers must provide sufficient trust metadata through "
                    f"a trust bundle or peer trust anchor: {missing}"
                )

        if self.real_internet == GatewayRealInternetMode.ISOLATED and (
            self.upstream_resolver_enabled or self.upstream_resolver_ip is not None
        ):
            raise ValueError("isolated mode should not enable upstream resolution")

        return self
