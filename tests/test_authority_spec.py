"""Tests for foundational authority spec primitives."""

import pytest
from pydantic import ValidationError

from netengine.spec import Authority, AuthorityKind, AuthorityScope, AuthoritySource, BoundaryPolicy
from netengine.spec.models import CrossWorldPeer, ServiceMirror
from netengine.spec.types import GatewayCrossWorldMode, GatewayRealInternetMode


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


def test_boundary_policy_defaults_to_isolated_no_cross_world() -> None:
    policy = BoundaryPolicy()

    assert policy.real_internet == GatewayRealInternetMode.ISOLATED
    assert policy.cross_world == GatewayCrossWorldMode.NONE
    assert policy.service_mirrors == []
    assert policy.upstream_resolver_enabled is False
    assert policy.upstream_resolver_ip is None
    assert policy.peers == []


def test_boundary_policy_mirrored_requires_service_mirror() -> None:
    with pytest.raises(ValidationError, match="requires at least one service mirror"):
        BoundaryPolicy(real_internet=GatewayRealInternetMode.MIRRORED)


def test_boundary_policy_service_mirrors_require_mirrored() -> None:
    with pytest.raises(ValidationError, match="service mirrors require"):
        BoundaryPolicy(
            service_mirrors=[
                ServiceMirror(real_hostname="api.example.com", in_world_service="10.1.2.3")
            ]
        )


def test_boundary_policy_peered_requires_peers() -> None:
    with pytest.raises(ValidationError, match="requires peers"):
        BoundaryPolicy(cross_world=GatewayCrossWorldMode.PEERED)


def test_boundary_policy_federated_requires_peer_trust_metadata() -> None:
    with pytest.raises(ValidationError, match="trust bundle or peer trust anchor"):
        BoundaryPolicy(
            cross_world=GatewayCrossWorldMode.FEDERATED,
            peers=[CrossWorldPeer(name="world-b", endpoint="10.99.0.1:8443")],
        )


def test_boundary_policy_federated_accepts_peer_trust_bundle() -> None:
    policy = BoundaryPolicy(
        cross_world=GatewayCrossWorldMode.FEDERATED,
        peers=[
            CrossWorldPeer(
                name="world-b",
                endpoint="10.99.0.1:8443",
                trust_bundle="spiffe://world-b.example/bundle",
            )
        ],
    )

    assert policy.peers[0].trust_bundle == "spiffe://world-b.example/bundle"


def test_boundary_policy_isolated_disallows_upstream_resolution() -> None:
    with pytest.raises(ValidationError, match="isolated mode should not enable upstream"):
        BoundaryPolicy(upstream_resolver_enabled=True, upstream_resolver_ip="8.8.8.8")
