"""Tests for foundational authority spec primitives."""

import pytest
from pydantic import ValidationError

from netengine.spec import (
    Authority,
    AuthorityKind,
    AuthorityScope,
    AuthoritySource,
    BoundaryPolicy,
    ResolverPolicy,
    TrustBundle,
    default_authorities_for_spec,
    resolver_policy_from_boundary,
)
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


def test_trust_bundle_accepts_peered_dns_suffixes_without_trust_metadata() -> None:
    bundle = TrustBundle(
        peer_id="world-b",
        peer_name="World B",
        mode=GatewayCrossWorldMode.PEERED,
        dns_suffixes=["world-b.test"],
    )

    assert bundle.peer_id == "world-b"
    assert bundle.peer_name == "World B"
    assert bundle.mode == GatewayCrossWorldMode.PEERED
    assert bundle.dns_suffixes == ["world-b.test"]
    assert bundle.accepted_audiences == []
    assert bundle.mail_domains == []


def test_trust_bundle_rejects_none_mode() -> None:
    with pytest.raises(ValidationError, match="must be peered or federated"):
        TrustBundle(
            peer_id="world-b",
            mode=GatewayCrossWorldMode.NONE,
            dns_suffixes=["world-b.test"],
        )


def test_trust_bundle_requires_dns_suffixes() -> None:
    with pytest.raises(ValidationError, match="dns_suffixes must not be empty"):
        TrustBundle(peer_id="world-b", mode=GatewayCrossWorldMode.PEERED, dns_suffixes=[])


def test_trust_bundle_federated_requires_trust_bearing_metadata() -> None:
    with pytest.raises(ValidationError, match="trust-bearing field"):
        TrustBundle(
            peer_id="world-b",
            mode=GatewayCrossWorldMode.FEDERATED,
            dns_suffixes=["world-b.test"],
        )


def test_trust_bundle_federated_accepts_peer_root_ca_or_oidc_issuer() -> None:
    ca_bundle = TrustBundle(
        peer_id="world-b",
        mode=GatewayCrossWorldMode.FEDERATED,
        dns_suffixes=["world-b.test"],
        peer_root_ca="-----BEGIN CERTIFICATE-----...",
    )
    oidc_bundle = TrustBundle(
        peer_id="world-c",
        mode="federated",
        dns_suffixes=["world-c.test"],
        oidc_issuer="https://issuer.world-c.test",
        accepted_audiences=["netengine"],
    )

    assert ca_bundle.peer_root_ca == "-----BEGIN CERTIFICATE-----..."
    assert oidc_bundle.mode == GatewayCrossWorldMode.FEDERATED
    assert oidc_bundle.oidc_issuer == "https://issuer.world-c.test"
    assert oidc_bundle.accepted_audiences == ["netengine"]


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


def test_resolver_policy_defaults_to_local_root_only() -> None:
    policy = ResolverPolicy()

    assert policy.local_root is True
    assert policy.upstream is False
    assert policy.mirror_table is False
    assert policy.peer_suffix_delegation is False
    assert policy.imported_trust_bundle is False
    assert policy.notes == []


def test_resolver_policy_from_isolated_boundary_uses_local_root_only() -> None:
    resolver_policy = resolver_policy_from_boundary(BoundaryPolicy())

    assert resolver_policy.local_root is True
    assert resolver_policy.upstream is False
    assert resolver_policy.mirror_table is False
    assert resolver_policy.peer_suffix_delegation is False
    assert resolver_policy.imported_trust_bundle is False
    assert resolver_policy.notes == ["isolated: local root only"]


def test_resolver_policy_from_shadowed_boundary_enables_upstream() -> None:
    resolver_policy = resolver_policy_from_boundary(
        BoundaryPolicy(real_internet=GatewayRealInternetMode.SHADOWED)
    )

    assert resolver_policy.local_root is True
    assert resolver_policy.upstream is True
    assert resolver_policy.mirror_table is False
    assert resolver_policy.notes == ["shadowed: local root first, upstream second"]


def test_resolver_policy_from_mirrored_boundary_uses_mirror_table_without_upstream() -> None:
    resolver_policy = resolver_policy_from_boundary(
        BoundaryPolicy(
            real_internet=GatewayRealInternetMode.MIRRORED,
            service_mirrors=[
                ServiceMirror(real_hostname="api.example.com", in_world_service="10.1.2.3")
            ],
            upstream_resolver_enabled=True,
        )
    )

    assert resolver_policy.local_root is True
    assert resolver_policy.upstream is False
    assert resolver_policy.mirror_table is True
    assert resolver_policy.notes == [
        "mirrored: mirror table first, local root second; upstream disabled"
    ]


def test_resolver_policy_from_peered_boundary_delegates_peer_suffixes_without_trust() -> None:
    resolver_policy = resolver_policy_from_boundary(
        BoundaryPolicy(
            cross_world=GatewayCrossWorldMode.PEERED,
            peers=[CrossWorldPeer(name="world-b", endpoint="10.99.0.1:8443")],
        )
    )

    assert resolver_policy.local_root is True
    assert resolver_policy.peer_suffix_delegation is True
    assert resolver_policy.imported_trust_bundle is False
    assert resolver_policy.notes == [
        "isolated: local root only",
        "peered: peer suffix delegation without shared trust",
    ]


def test_resolver_policy_from_federated_boundary_imports_trust_bundle() -> None:
    resolver_policy = resolver_policy_from_boundary(
        BoundaryPolicy(
            cross_world=GatewayCrossWorldMode.FEDERATED,
            peers=[
                CrossWorldPeer(
                    name="world-b",
                    endpoint="10.99.0.1:8443",
                    trust_bundle="spiffe://world-b.example/bundle",
                )
            ],
        )
    )

    assert resolver_policy.local_root is True
    assert resolver_policy.peer_suffix_delegation is True
    assert resolver_policy.imported_trust_bundle is True
    assert resolver_policy.notes == [
        "isolated: local root only",
        "federated: peer suffix delegation with imported trust bundle",
    ]


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


def test_world_manifest_from_spec_uses_metadata_name_and_default_authorities(minimal_spec) -> None:
    from netengine.spec import WorldManifest, world_manifest_from_spec

    manifest = world_manifest_from_spec(minimal_spec)

    assert isinstance(manifest, WorldManifest)
    assert manifest.world_id == minimal_spec.metadata.name
    assert manifest.world_name == minimal_spec.metadata.name
    assert manifest.lifecycle == minimal_spec.metadata.lifecycle
    assert manifest.authority_model == "default"
    assert [authority.id for authority in manifest.authorities] == [
        authority.id for authority in default_authorities_for_spec(minimal_spec)
    ]
    assert manifest.dns_root_authority == "root-naming"
    assert manifest.ca_trust_authority == "trust-root"
    assert manifest.platform_identity_issuer == "platform-identity"
    assert manifest.inworld_identity_issuer == "inworld-identity"
    assert manifest.world_registry_authority == "world-root"
    assert manifest.domain_registry_authority == "domain-registry"
    assert manifest.numbering_authority == "numbering"
    assert manifest.transit_boundary_authority == "transit-boundary"
    assert manifest.real_internet_posture == minimal_spec.gateway_portal.real_internet.mode
    assert manifest.cross_world_posture == minimal_spec.gateway_portal.cross_world.mode
    assert manifest.exported_authority_metadata == {}
    assert manifest.importable_authority_metadata == {}
    assert manifest.trust_bundles == []


def test_world_manifest_from_spec_derives_cross_world_trust_bundles(minimal_spec) -> None:
    from netengine.spec import world_manifest_from_spec

    spec = minimal_spec.model_copy(
        update={
            "gateway_portal": minimal_spec.gateway_portal.model_copy(
                update={
                    "cross_world": minimal_spec.gateway_portal.cross_world.model_copy(
                        update={
                            "mode": GatewayCrossWorldMode.FEDERATED,
                            "peers": [
                                CrossWorldPeer(
                                    name="world-b.internal",
                                    endpoint="10.99.0.1:8443",
                                    mode=GatewayCrossWorldMode.FEDERATED,
                                    trust_anchor_cert="-----BEGIN CERTIFICATE-----...",
                                )
                            ],
                        }
                    )
                }
            )
        }
    )

    manifest = world_manifest_from_spec(spec)

    assert manifest.cross_world_posture == GatewayCrossWorldMode.FEDERATED
    assert len(manifest.trust_bundles) == 1
    assert manifest.trust_bundles[0].peer_id == "world-b.internal"
    assert manifest.trust_bundles[0].peer_name == "world-b.internal"
    assert manifest.trust_bundles[0].mode == GatewayCrossWorldMode.FEDERATED
    assert manifest.trust_bundles[0].dns_suffixes == ["world-b.internal"]
    assert manifest.trust_bundles[0].peer_root_ca == "-----BEGIN CERTIFICATE-----..."


def test_authority_posture_is_optional_for_existing_examples(minimal_spec) -> None:
    from netengine.spec import AuthorityPosture

    assert minimal_spec.authority is None
    posture = AuthorityPosture(authority_model="custom")
    assert posture.authority_model == "custom"


def test_world_manifest_from_spec_applies_optional_authority_posture(minimal_spec) -> None:
    from netengine.spec import AuthorityPosture, world_manifest_from_spec

    spec = minimal_spec.model_copy(
        update={
            "authority": AuthorityPosture(
                authority_model="custom",
                dns_root_authority="custom-root",
                ca_trust_authority="custom-trust",
            )
        }
    )

    manifest = world_manifest_from_spec(spec)

    assert manifest.authority_model == "custom"
    assert manifest.dns_root_authority == "custom-root"
    assert manifest.ca_trust_authority == "custom-trust"
    assert manifest.platform_identity_issuer == "platform-identity"
