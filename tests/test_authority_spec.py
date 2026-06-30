"""Tests for foundational authority spec primitives."""

import pytest
from pydantic import ValidationError

from netengine.spec import Authority, AuthorityKind, AuthorityScope, AuthoritySource


def test_authority_defaults_to_local_source() -> None:
    authority = Authority(
        id="world-root",
        kind=AuthorityKind.WORLD_ROOT,
        scope=AuthorityScope.WORLD,
        operator="root-operator",
        controls=["dns.root", "pki.root_ca"],
    )

    assert authority.source == AuthoritySource.LOCAL
    assert authority.description is None


def test_authority_accepts_enum_values() -> None:
    authority = Authority(
        id="mail-authority",
        kind="mail",
        scope="org",
        operator="mail-ops",
        controls=["mx.example.internal"],
        source="mirrored",
    )

    assert authority.kind == AuthorityKind.MAIL
    assert authority.scope == AuthorityScope.ORG
    assert authority.source == AuthoritySource.MIRRORED


def test_authority_model_is_frozen() -> None:
    authority = Authority(
        id="registry-authority",
        kind=AuthorityKind.DOMAIN_REGISTRY,
        scope=AuthorityScope.INWORLD,
        operator="registry-ops",
        controls=["domains"],
    )

    with pytest.raises(ValidationError):
        authority.operator = "other-operator"
