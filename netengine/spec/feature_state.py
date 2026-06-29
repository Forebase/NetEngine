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
        state="unsupported",
        stage="alpha",
        reason="DNSSEC key generation is not integrated with zone signing",
    ),
    FeatureStateEntry(
        path="pki.crl_enabled",
        state="unsupported",
        stage="alpha",
        reason="CRL publication and distribution points are not implemented",
    ),
    FeatureStateEntry(
        path="pki.ocsp_enabled",
        state="unsupported",
        stage="alpha",
        reason="OCSP responder deployment is not implemented",
    ),
    FeatureStateEntry(
        path="gateway_portal.real_internet.mode",
        state="unsupported",
        stage="alpha",
        reason="real-internet gateway policies are not implemented",
    ),
    FeatureStateEntry(
        path="gateway_portal.real_internet.service_mirrors",
        state="unsupported",
        stage="alpha",
        reason="service mirror provisioning is not implemented",
    ),
    FeatureStateEntry(
        path="gateway_portal.real_internet.upstream_resolver_enabled",
        state="unsupported",
        stage="alpha",
        reason="upstream resolver forwarding is not implemented",
    ),
    FeatureStateEntry(
        path="gateway_portal.cross_world.mode",
        state="unsupported",
        stage="alpha",
        reason="cross-world federation is not implemented",
    ),
    FeatureStateEntry(
        path="gateway_portal.cross_world.peers",
        state="unsupported",
        stage="alpha",
        reason="cross-world peer provisioning is not implemented",
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
