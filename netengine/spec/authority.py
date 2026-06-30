"""Foundational authority primitives for NetEngine specifications."""

from enum import Enum
from pydantic import Field

from netengine.spec.models import SpecModel


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
