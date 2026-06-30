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


def test_default_authorities_for_spec_maps_spec_sections(minimal_spec) -> None:
    from netengine.spec import default_authorities_for_spec

    authorities = default_authorities_for_spec(minimal_spec)
    by_id = {authority.id: authority for authority in authorities}

    assert list(by_id) == [
        "world-root",
        "root-naming",
        "numbering",
        "domain-registry",
        "default-registrar",
        "trust-root",
        "platform-identity",
        "inworld-identity",
        "transit-boundary",
        "mail-authority",
        "service-catalog",
    ]
    assert by_id["root-naming"].kind == AuthorityKind.ROOT_NAMING
    assert by_id["root-naming"].controls == ["dns.root", "dns.tlds"]
    assert by_id["numbering"].kind == AuthorityKind.NUMBERING
    assert by_id["numbering"].controls == [
        "domain_registry.address_space",
        "substrate.networks.core",
    ]
    assert by_id["world-root"].controls == ["world_registry"]
    assert by_id["domain-registry"].controls == ["domain_registry"]
    assert by_id["default-registrar"].controls == ["domain_registry.registrar"]
    assert by_id["trust-root"].controls == ["pki.root_ca", "pki.acme"]
    assert by_id["platform-identity"].controls == ["identity_platform"]
    assert by_id["inworld-identity"].controls == ["identity_inworld"]
    assert by_id["transit-boundary"].controls == [
        "gateway_portal",
        "gateway_portal.real_internet",
        "gateway_portal.cross_world",
    ]
    assert by_id["mail-authority"].controls == ["world_services.mail"]
    assert by_id["service-catalog"].controls == ["org_apps.catalog"]
