"""Spec parsing and models for NetEngine declarative specifications."""

from netengine.spec.authority import (
    Authority,
    AuthorityKind,
    AuthorityScope,
    AuthoritySource,
    default_authorities_for_spec,
)

__all__ = [
    "Authority",
    "AuthorityKind",
    "AuthorityScope",
    "AuthoritySource",
    "default_authorities_for_spec",
]
