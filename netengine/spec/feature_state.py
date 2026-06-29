"""Feature-state metadata for spec fields that are not generally available."""

from dataclasses import dataclass
from typing import Literal

FeatureState = Literal["unsupported", "experimental"]


@dataclass(frozen=True)
class FeatureStateEntry:
    """Feature-state metadata for a spec field path."""

    path: str
    state: FeatureState
    stage: str
    reason: str


FEATURE_STATE_REGISTRY: tuple[FeatureStateEntry, ...] = (
    FeatureStateEntry(
        path="pki.dnssec_enabled",
        state="experimental",
        stage="alpha",
        reason=(
            "DNSSEC key generation is wired into CoreDNS online signing with "
            "KSK/ZSK rotation; end-to-end signed-zone validation is still being "
            "hardened in CI e2e"
        ),
    ),
    FeatureStateEntry(
        path="pki.crl_enabled",
        state="experimental",
        stage="alpha",
        reason=(
            "step-ca CRL generation is enabled and the distribution URL is "
            "published; client-validation coverage is still being hardened in CI e2e"
        ),
    ),
    FeatureStateEntry(
        path="pki.ocsp_enabled",
        state="experimental",
        stage="alpha",
        reason=(
            "step-ca OCSP config is injected and the responder URL is published; "
            "responder lifecycle/verification is still being hardened in CI e2e"
        ),
    ),
    FeatureStateEntry(
        path="gateway_portal.real_internet.mode",
        state="experimental",
        stage="alpha",
        reason=(
            "nftables policies for isolated/shadowed/mirrored/exposed modes are "
            "implemented; requires gateway container with nft available"
        ),
    ),
    FeatureStateEntry(
        path="gateway_portal.real_internet.service_mirrors",
        state="experimental",
        stage="alpha",
        reason=(
            "mirror accept rules are generated in mirrored mode; "
            "live upstream reachability is not validated in CI e2e"
        ),
    ),
    FeatureStateEntry(
        path="gateway_portal.real_internet.upstream_resolver_enabled",
        state="experimental",
        stage="alpha",
        reason=(
            "upstream forwarder is appended to the CoreDNS Corefile and CoreDNS "
            "is reloaded; requires a reachable resolver at upstream_resolver_ip"
        ),
    ),
    FeatureStateEntry(
        path="gateway_portal.cross_world.mode",
        state="experimental",
        stage="alpha",
        reason=(
            "PEERED mode wires nftables peer routing, trust-anchor install, and "
            "CoreDNS forwarding stubs; live cross-world DNS resolution needs two "
            "running worlds and is not covered by CI e2e"
        ),
    ),
    FeatureStateEntry(
        path="gateway_portal.cross_world.peers",
        state="experimental",
        stage="alpha",
        reason=(
            "per-peer routing rules and DNS forwarder stubs are provisioned; "
            "actual cross-world resolution requires a reachable peer endpoint"
        ),
    ),
    FeatureStateEntry(
        path="ands.profiles.*.dynamic_ip",
        state="experimental",
        stage="alpha",
        reason="DHCP via dnsmasq in gateway container; requires dnsmasq installed in gateway image",
    ),
    FeatureStateEntry(
        path="ands.profiles.*.reverse_dns",
        state="experimental",
        stage="alpha",
        reason="in-addr.arpa zone provisioning available; not yet propagated to external resolvers",
    ),
    FeatureStateEntry(
        path="ands.profiles.*.bgp",
        state="experimental",
        stage="alpha",
        reason="Bird2 BGP speaker sidecar; requires pierrecdn/bird:2.0.9 image available",
    ),
    FeatureStateEntry(
        path="pki.intermediate_ca_enabled",
        state="experimental",
        stage="alpha",
        reason="intermediate CA handling is available but still stabilizing",
    ),
)
