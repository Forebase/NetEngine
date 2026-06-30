"""Foundational authority primitives for NetEngine specifications."""

from enum import Enum
from pydantic import Field, model_validator
from typing import TYPE_CHECKING, Any


from netengine.spec.models import CrossWorldPeer, ServiceMirror, SpecModel
from netengine.spec.types import GatewayCrossWorldMode, GatewayRealInternetMode, Lifecycle

if TYPE_CHECKING:
    from netengine.spec.models import NetEngineSpec


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


class ResolverPolicy(SpecModel):
    """Derived resolver capabilities for a boundary authority posture."""

    local_root: bool = Field(default=True)
    upstream: bool = Field(default=False)
    mirror_table: bool = Field(default=False)
    peer_suffix_delegation: bool = Field(default=False)
    imported_trust_bundle: bool = Field(default=False)
    notes: list[str] = Field(default_factory=list)


class TrustBundle(SpecModel):
    """Trust metadata imported for a peered or federated world boundary."""

    peer_id: str = Field(...)
    peer_name: str | None = Field(default=None)
    mode: GatewayCrossWorldMode = Field(...)
    dns_suffixes: list[str] = Field(default_factory=list)
    peer_root_ca: str | None = Field(default=None)
    oidc_issuer: str | None = Field(default=None)
    accepted_audiences: list[str] = Field(default_factory=list)
    mail_domains: list[str] = Field(default_factory=list)
    dkim_policy: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_trust_bundle(self) -> "TrustBundle":
        """Validate cross-world trust metadata requirements."""
        if self.mode == GatewayCrossWorldMode.NONE:
            raise ValueError("trust bundle mode must be peered or federated, not none")

        if not self.dns_suffixes:
            raise ValueError("trust bundle dns_suffixes must not be empty")

        if self.mode == GatewayCrossWorldMode.FEDERATED and not (
            self.peer_root_ca or self.oidc_issuer
        ):
            raise ValueError(
                "federated trust bundle requires at least one trust-bearing field "
                "such as peer_root_ca or oidc_issuer"
            )

        return self


class WorldManifest(SpecModel):
    """Authority-layer manifest for a NetEngine world.

    The manifest provides a compact authority/spec-domain view of the
    governance, naming, identity, trust, registry, numbering, and boundary
    surfaces that define a world.
    """

    world_id: str = Field(...)
    world_name: str = Field(...)
    lifecycle: Lifecycle = Field(...)
    authority_model: str = Field(...)
    authorities: list[Authority] = Field(default_factory=list)
    dns_root_authority: str = Field(...)
    ca_trust_authority: str = Field(...)
    platform_identity_issuer: str = Field(...)
    inworld_identity_issuer: str = Field(...)
    world_registry_authority: str = Field(...)
    domain_registry_authority: str = Field(...)
    numbering_authority: str = Field(...)
    transit_boundary_authority: str = Field(...)
    real_internet_posture: GatewayRealInternetMode = Field(...)
    cross_world_posture: GatewayCrossWorldMode = Field(...)
    exported_authority_metadata: dict[str, Any] = Field(default_factory=dict)
    importable_authority_metadata: dict[str, Any] = Field(default_factory=dict)
    trust_bundles: list[TrustBundle] = Field(default_factory=list)


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


def resolver_policy_from_boundary(policy: BoundaryPolicy) -> ResolverPolicy:
    """Derive resolver behavior from a boundary policy.

    The result intentionally models resolver capabilities rather than concrete
    DNS server ordering. Notes capture the intended precedence for modes where
    ordering matters.
    """

    notes: list[str] = []
    upstream = False
    mirror_table = False

    if policy.real_internet == GatewayRealInternetMode.ISOLATED:
        notes.append("isolated: local root only")
    elif policy.real_internet == GatewayRealInternetMode.SHADOWED:
        upstream = True
        notes.append("shadowed: local root first, upstream second")
    elif policy.real_internet == GatewayRealInternetMode.MIRRORED:
        mirror_table = True
        notes.append("mirrored: mirror table first, local root second; upstream disabled")
    else:
        upstream = policy.upstream_resolver_enabled
        notes.append(
            f"{policy.real_internet.value}: no minimum resolver semantics beyond configured upstream"
        )

    peer_suffix_delegation = policy.cross_world in {
        GatewayCrossWorldMode.PEERED,
        GatewayCrossWorldMode.FEDERATED,
    }
    imported_trust_bundle = policy.cross_world == GatewayCrossWorldMode.FEDERATED

    if policy.cross_world == GatewayCrossWorldMode.PEERED:
        notes.append("peered: peer suffix delegation without shared trust")
    elif policy.cross_world == GatewayCrossWorldMode.FEDERATED:
        notes.append("federated: peer suffix delegation with imported trust bundle")

    return ResolverPolicy(
        local_root=True,
        upstream=upstream,
        mirror_table=mirror_table,
        peer_suffix_delegation=peer_suffix_delegation,
        imported_trust_bundle=imported_trust_bundle,
        notes=notes,
    )


def default_authorities_for_spec(spec: "NetEngineSpec") -> list[Authority]:
    """Return stable default authorities for the control surfaces in ``spec``.

    The default authority set is derived from the existing top-level spec
    sections. Each returned authority uses a stable id so downstream runtime
    state, audit records, and generated artifacts can refer to authority
    surfaces consistently across loads of the same spec.
    """

    core_network_controls = [
        f"substrate.networks.{name}"
        for name in sorted(spec.substrate.networks)
        if name == "core" or name.startswith("core-") or name.startswith("core_")
    ]

    return [
        Authority(
            id="world-root",
            kind=AuthorityKind.WORLD_ROOT,
            scope=AuthorityScope.WORLD,
            operator="world_registry",
            controls=["world_registry"],
            description="Root governance authority for world admission and policy.",
        ),
        Authority(
            id="root-naming",
            kind=AuthorityKind.ROOT_NAMING,
            scope=AuthorityScope.WORLD,
            operator="dns",
            controls=["dns.root", "dns.tlds"],
            description="Authority over the in-world DNS root and TLD delegations.",
        ),
        Authority(
            id="numbering",
            kind=AuthorityKind.NUMBERING,
            scope=AuthorityScope.WORLD,
            operator="domain_registry",
            controls=["domain_registry.address_space", *core_network_controls],
            description="Authority over address-space allocation and core network pools.",
        ),
        Authority(
            id="domain-registry",
            kind=AuthorityKind.DOMAIN_REGISTRY,
            scope=AuthorityScope.INWORLD,
            operator="domain_registry",
            controls=["domain_registry"],
            description="Authority over in-world domain resources and registry records.",
        ),
        Authority(
            id="default-registrar",
            kind=AuthorityKind.REGISTRAR,
            scope=AuthorityScope.INWORLD,
            operator="domain_registry.registrar",
            controls=["domain_registry.registrar"],
            description="Default registrar authority for domain registration workflows.",
        ),
        Authority(
            id="trust-root",
            kind=AuthorityKind.TRUST,
            scope=AuthorityScope.WORLD,
            operator="pki",
            controls=["pki.root_ca", "pki.acme"],
            description="Authority over root trust and certificate issuance.",
        ),
        Authority(
            id="platform-identity",
            kind=AuthorityKind.PLATFORM_IDENTITY,
            scope=AuthorityScope.PLATFORM,
            operator="identity_platform",
            controls=["identity_platform"],
            description="Authority over platform operator identity.",
        ),
        Authority(
            id="inworld-identity",
            kind=AuthorityKind.INWORLD_IDENTITY,
            scope=AuthorityScope.INWORLD,
            operator="identity_inworld",
            controls=["identity_inworld"],
            description="Authority over in-world inhabitants and organization users.",
        ),
        Authority(
            id="transit-boundary",
            kind=AuthorityKind.TRANSIT,
            scope=AuthorityScope.BOUNDARY,
            operator="gateway_portal",
            controls=[
                "gateway_portal",
                "gateway_portal.real_internet",
                "gateway_portal.cross_world",
            ],
            description="Authority over gateway portal transit and boundary posture.",
        ),
        Authority(
            id="mail-authority",
            kind=AuthorityKind.MAIL,
            scope=AuthorityScope.WORLD,
            operator="world_services.mail",
            controls=["world_services.mail"],
            description="Authority over world mail service configuration.",
        ),
        Authority(
            id="service-catalog",
            kind=AuthorityKind.SERVICE_CATALOG,
            scope=AuthorityScope.WORLD,
            operator="org_apps.catalog",
            controls=["org_apps.catalog"],
            description="Authority over the application service catalog.",
        ),
    ]


def _trust_bundles_from_spec(spec: "NetEngineSpec") -> list[TrustBundle]:
    """Derive importable trust bundles from configured cross-world peers."""

    bundles: list[TrustBundle] = []
    for peer in spec.gateway_portal.cross_world.peers:
        mode = peer.mode
        if mode == GatewayCrossWorldMode.NONE:
            continue

        if mode == GatewayCrossWorldMode.FEDERATED and not (
            peer.trust_anchor_cert or peer.trust_bundle
        ):
            continue

        bundles.append(
            TrustBundle(
                peer_id=peer.name,
                peer_name=peer.name,
                mode=mode,
                dns_suffixes=[peer.name],
                peer_root_ca=peer.trust_anchor_cert,
                oidc_issuer=peer.trust_bundle,
            )
        )

    return bundles


def world_manifest_from_spec(spec: "NetEngineSpec") -> WorldManifest:
    """Build an authority-layer world manifest from a parsed NetEngine spec.

    NetEngine specs do not currently expose a dedicated stronger world-id field,
    so the manifest intentionally seeds both identity fields from
    ``spec.metadata.name``.
    """

    authorities = default_authorities_for_spec(spec)

    posture = spec.authority

    return WorldManifest(
        world_id=spec.metadata.name,
        world_name=spec.metadata.name,
        lifecycle=spec.metadata.lifecycle,
        authority_model=posture.authority_model if posture else "default",
        authorities=authorities,
        dns_root_authority=(posture.dns_root_authority if posture else None) or "root-naming",
        ca_trust_authority=(posture.ca_trust_authority if posture else None) or "trust-root",
        platform_identity_issuer=(
            (posture.platform_identity_issuer if posture else None) or "platform-identity"
        ),
        inworld_identity_issuer=(
            (posture.inworld_identity_issuer if posture else None) or "inworld-identity"
        ),
        world_registry_authority=(
            (posture.world_registry_authority if posture else None) or "world-root"
        ),
        domain_registry_authority=(
            (posture.domain_registry_authority if posture else None) or "domain-registry"
        ),
        numbering_authority=(posture.numbering_authority if posture else None) or "numbering",
        transit_boundary_authority=(
            (posture.transit_boundary_authority if posture else None) or "transit-boundary"
        ),
        real_internet_posture=spec.gateway_portal.real_internet.mode,
        cross_world_posture=spec.gateway_portal.cross_world.mode,
        trust_bundles=_trust_bundles_from_spec(spec),
    )
