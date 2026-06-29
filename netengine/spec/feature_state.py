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
        state="unsupported",
        stage="alpha",
        reason="dynamic IP allocation is not implemented",
    ),
    FeatureStateEntry(
        path="ands.profiles.*.reverse_dns",
        state="unsupported",
        stage="alpha",
        reason="reverse DNS delegation is not implemented",
    ),
    FeatureStateEntry(
        path="ands.profiles.*.bgp",
        state="unsupported",
        stage="alpha",
        reason="BGP profile configuration is not implemented",
    ),
    FeatureStateEntry(
        path="pki.intermediate_ca_enabled",
        state="experimental",
        stage="alpha",
        reason="intermediate CA handling is available but still stabilizing",
    ),
)
