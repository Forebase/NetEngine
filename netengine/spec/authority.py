"""Foundational authority primitives for NetEngine specifications."""

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import Field

from netengine.spec.models import SpecModel

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
