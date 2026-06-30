"""Pydantic v2 models for NetEngine declarative specifications (netengines-spec-v0.2)."""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from netengine.spec.types import (
    ANDProfile,
    AppScope,
    GatewayCrossWorldMode,
    GatewayRealInternetMode,
    Lifecycle,
    OperatorRole,
    Orchestrator,
    SerialPolicy,
)


class SpecModel(BaseModel):
    """Base model for all spec components. Frozen (immutable) at parse time."""

    model_config = ConfigDict(frozen=True)


class FeatureState(str, Enum):
    """Implementation maturity for spec fields.

    Values are serialized into JSON Schema so validators, tooling, and
    future documentation generators can discover whether a field is stable,
    experimental, reserved for future use, or currently unsupported.
    """

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    RESERVED = "reserved"
    UNSUPPORTED = "unsupported"


FEATURE_STATE_JSON_SCHEMA_KEY = "feature_state"
SPEC_SCHEMA_VERSION = "netengine.spec.v1"
SUPPORTED_SPEC_SCHEMA_VERSIONS = {SPEC_SCHEMA_VERSION}


def feature_state_extra(feature_state: FeatureState) -> dict[str, str]:
    """Return JSON Schema metadata for a field's feature state."""

    return {FEATURE_STATE_JSON_SCHEMA_KEY: feature_state.value}


def feature_state_field(
    default: Any = ...,
    *,
    feature_state: FeatureState,
    json_schema_extra: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> Any:
    """Create a Pydantic field with discoverable feature-state metadata."""

    extra: dict[str, Any] = feature_state_extra(feature_state)
    if json_schema_extra:
        extra.update(json_schema_extra)
    return Field(default, json_schema_extra=extra, **kwargs)


PKI_FEATURE_STATES: dict[str, FeatureState] = {
    "pki.intermediate_ca_enabled": FeatureState.EXPERIMENTAL,
    "pki.dnssec_enabled": FeatureState.EXPERIMENTAL,
    "pki.dnssec_ksk_lifetime_days": FeatureState.EXPERIMENTAL,
    "pki.dnssec_zsk_lifetime_days": FeatureState.EXPERIMENTAL,
    "pki.crl_enabled": FeatureState.EXPERIMENTAL,
    "pki.ocsp_enabled": FeatureState.EXPERIMENTAL,
    "pki.rotation_policy": FeatureState.EXPERIMENTAL,
}


# ─────────────────────────────────────────────
# METADATA
# ─────────────────────────────────────────────


class SpecMetadata(SpecModel):
    """Top-level spec metadata."""

    schema_version: str = Field(
        default=SPEC_SCHEMA_VERSION,
        description="NetEngine spec schema/version identifier used for compatibility checks",
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        """Reject unsupported spec schemas before boot/import."""
        if value not in SUPPORTED_SPEC_SCHEMA_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_SPEC_SCHEMA_VERSIONS))
            raise ValueError(
                f"unsupported spec schema_version {value!r}; supported versions: {supported}"
            )
        return value

    name: str = Field(..., description="World name")
    version: str = Field(default="1.0", description="Spec version")
    lifecycle: Lifecycle = Field(
        default=Lifecycle.EPHEMERAL,
        description="ephemeral or persistent",
        json_schema_extra={
            "immutable": True,
            "immutable_reason": "Ephemeral ↔ persistent requires explicit migration, not a reload",
        },
    )
    organization: Optional[str] = Field(default=None, description="Owner organization (optional)")
    environment: Optional[str] = Field(default=None, description="Environment label (optional)")


# ─────────────────────────────────────────────
# PHASE 0: SUBSTRATE
# ─────────────────────────────────────────────


class NTPConfig(SpecModel):
    """NTP time synchronization config."""

    enabled: bool = Field(default=True)
    servers: list[str] = Field(default_factory=lambda: ["pool.ntp.org"])


class NetworkConfig(SpecModel):
    """Docker bridge network definition."""

    type: str = Field(default="bridge")
    subnet: str = Field(..., description="CIDR block")
    description: Optional[str] = None


class GatewaySubstrate(SpecModel):
    """Gateway stub at Phase 0 (policy applied later in Phase 7)."""

    platform_ip: str = Field(
        ...,
        description="IP on platform network",
        json_schema_extra={
            "immutable": True,
            "immutable_reason": "Hardcoded into every resolver config — reset required",
        },
    )
    core_ip: str = Field(
        ...,
        description="IP on core network",
        json_schema_extra={
            "immutable": True,
            "immutable_reason": "Hardcoded into every resolver config — reset required",
        },
    )
    description: Optional[str] = None


class SubstratePhase(SpecModel):
    """Phase 0: Substrate — pre-naming, pre-PKI substrate."""

    orchestrator: Orchestrator = Field(default=Orchestrator.SWARM)
    ntp: NTPConfig = Field(default_factory=NTPConfig)
    networks: dict[str, NetworkConfig] = Field(
        default_factory=lambda: {
            "platform": NetworkConfig(subnet="172.28.0.0/16"),
            "core": NetworkConfig(subnet="10.0.0.0/4"),
        },
        json_schema_extra={
            "immutable": True,
            "immutable_reason": "L0 Docker bridge CIDRs underpin every container IP"
            " — reset required",
        },
    )
    gateway: GatewaySubstrate = Field(..., description="Gateway stub configuration")


# ─────────────────────────────────────────────
# PHASE 1-2: DNS
# ─────────────────────────────────────────────


class RootDNSConfig(SpecModel):
    """Phase 1: DNS Root."""

    enabled: bool = Field(default=True)
    type: str = Field(default="authoritative")
    server: str = Field(default="coredns")
    listen_ip: str = Field(
        default="10.0.0.2",
        json_schema_extra={
            "immutable": True,
            "immutable_reason": "Hardcoded into every container resolver config — reset required",
        },
    )
    soa_primary_ns: str = Field(default="root.internal")
    soa_email: str = Field(default="admin.internal")
    serial_policy: SerialPolicy = Field(default=SerialPolicy.TIMESTAMP)


class PlatformZoneConfig(SpecModel):
    """Platform zone (L1 service names)."""

    name: str = Field(default="platform.internal")
    type: str = Field(default="authoritative")
    listen_ip: str = Field(default="10.0.0.3")


class TLDConfig(SpecModel):
    """TLD server definition."""

    name: str = Field(..., description="TLD name (e.g., '.internal')")
    description: Optional[str] = None
    type: str = Field(default="authoritative")
    listen_ip: str = Field(...)


class DNSPhase(SpecModel):
    """Phases 1-2: DNS Root and Hierarchy."""

    root: RootDNSConfig = Field(default_factory=RootDNSConfig)
    platform_zone: PlatformZoneConfig = Field(default_factory=PlatformZoneConfig)
    tlds: list[TLDConfig] = Field(default_factory=list)


# ─────────────────────────────────────────────
# PHASE 3: PKI
# ─────────────────────────────────────────────


class RootCAConfig(SpecModel):
    """Root CA definition."""

    cn: str = Field(default="NetEngines Root CA")
    o: str = Field(default="NetEngines")
    c: str = Field(default="US")
    key_storage_mode: Lifecycle = Field(
        default=Lifecycle.EPHEMERAL,
        description="ephemeral (generated at spinup) or persistent (durable)",
    )
    cert_lifetime_days: int = Field(default=3650)


class ACMEConfig(SpecModel):
    """ACME provisioner config."""

    enabled: bool = Field(default=True)
    listen_ip: str = Field(
        default="10.0.0.6",
        json_schema_extra={
            "immutable": True,
            "immutable_reason": (
                "Hardcoded into every service ACME config and trust store — reset required"
            ),
        },
    )
    canonical_name: str = Field(default="ca.platform.internal")


class CertTypeRotationConfig(SpecModel):
    """Rotation policy for a specific certificate type."""

    cert_type: str = Field(
        ..., description="Certificate type (platform_identity, app, storage, etc.)"
    )
    rotation_interval_hours: int = Field(default=24, description="Check cert expiry every N hours")
    expiry_warning_days: int = Field(default=30, description="Rotate certs expiring within N days")


class PKIRotationPolicy(SpecModel):
    """Overall PKI certificate rotation policy."""

    enabled: bool = Field(default=True, description="Enable automatic certificate rotation")
    default_interval_hours: int = Field(
        default=24, description="Default check interval for all cert types"
    )
    default_warning_days: int = Field(
        default=30, description="Default expiry warning threshold for all cert types"
    )
    cert_type_overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description="Per-cert-type config overrides (keys are cert_type, values are dicts)",
    )


class PKIPhase(SpecModel):
    """Phase 3: PKI and ACME."""

    root_ca: RootCAConfig = Field(default_factory=RootCAConfig)
    acme: ACMEConfig = Field(default_factory=ACMEConfig)
    intermediate_ca_enabled: bool = feature_state_field(
        default=False, feature_state=PKI_FEATURE_STATES["pki.intermediate_ca_enabled"]
    )
    dnssec_enabled: bool = feature_state_field(
        default=False, feature_state=PKI_FEATURE_STATES["pki.dnssec_enabled"]
    )
    dnssec_ksk_lifetime_days: int = feature_state_field(
        default=365, feature_state=PKI_FEATURE_STATES["pki.dnssec_ksk_lifetime_days"]
    )
    dnssec_zsk_lifetime_days: int = feature_state_field(
        default=30, feature_state=PKI_FEATURE_STATES["pki.dnssec_zsk_lifetime_days"]
    )
    crl_enabled: bool = feature_state_field(
        default=False, feature_state=PKI_FEATURE_STATES["pki.crl_enabled"]
    )
    ocsp_enabled: bool = feature_state_field(
        default=False, feature_state=PKI_FEATURE_STATES["pki.ocsp_enabled"]
    )
    rotation_policy: PKIRotationPolicy = feature_state_field(
        default_factory=PKIRotationPolicy,
        feature_state=PKI_FEATURE_STATES["pki.rotation_policy"],
    )


# ─────────────────────────────────────────────
# PHASE 4: PLATFORM IDENTITY (L1)
# ─────────────────────────────────────────────


class AdminUser(SpecModel):
    """Admin user for platform realm."""

    username: str = Field(default="admin")
    email: str = Field(default="admin@platform.internal")


class IdentityPlatformPhase(SpecModel):
    """Phase 4: Platform Identity (L1 — who may operate NetEngines)."""

    oidc_provider: str = Field(default="keycloak")
    listen_ip: str = Field(default="10.0.0.7")
    canonical_name: str = Field(default="auth.platform.internal")
    realm_name: str = Field(default="platform")
    admin_user: AdminUser = Field(default_factory=AdminUser)
    scopes: list[str] = Field(
        default_factory=lambda: ["netengines:read", "netengines:write", "netengines:admin"]
    )


# ─────────────────────────────────────────────
# PHASE 5: REGISTRIES
# ─────────────────────────────────────────────


class Capability(str, Enum):
    """Org capability grants."""

    HOST_SERVICES = "host_services"
    SEND_MAIL = "send_mail"
    REGISTER_DOMAINS = "register_domains"


class Organization(SpecModel):
    """Organization admitted to the world."""

    name: str = Field(...)
    description: Optional[str] = None
    capabilities: list[Capability] = Field(default_factory=list)
    and_profile: ANDProfile = Field(default=ANDProfile.BUSINESS)


class Operator(SpecModel):
    """World operator with platform access."""

    username: str = Field(...)
    role: OperatorRole = Field(default=OperatorRole.READONLY)


class WHOISConfig(SpecModel):
    """WHOIS service config."""

    enabled: bool = Field(default=True)
    listen_ip: str = Field(default="10.0.0.9")
    port: int = Field(default=43)


class WorldRegistryPhase(SpecModel):
    """Phase 5a: World Registry (governance — who may exist in this world)."""

    enabled: bool = Field(default=True)
    listen_ip: str = Field(default="10.0.0.8")
    canonical_name: str = Field(default="registry.platform.internal")
    organizations: list[Organization] = Field(default_factory=list)
    operators: list[Operator] = Field(default_factory=list)
    whois: WHOISConfig = Field(default_factory=WHOISConfig)


class TLDDelegation(SpecModel):
    """TLD delegation record."""

    tld: str = Field(...)
    governed_by: str = Field(default="platform")


class AddressPool(SpecModel):
    """Address space allocation pool."""

    cidr: str = Field(...)
    label: str = Field(...)
    allocated_to: str = Field(default="")


class RegistrarConfig(SpecModel):
    """Domain registrar service."""

    enabled: bool = Field(default=True)
    listen_ip: str = Field(default="10.0.0.11")
    canonical_name: str = Field(default="registrar.platform.internal")


class DomainRegistryPhase(SpecModel):
    """Phase 5b: Domain Registry (resources — who owns what)."""

    enabled: bool = Field(default=True)
    listen_ip: str = Field(default="10.0.0.10")
    canonical_name: str = Field(default="domainreg.platform.internal")
    tld_delegations: list[TLDDelegation] = Field(default_factory=list)
    address_space: list[AddressPool] = Field(
        default_factory=list,
        json_schema_extra={
            "immutable": True,
            "immutable_reason": "Existing AND leases reference these CIDRs — new pool entries only",
        },
    )
    registrar: RegistrarConfig = Field(default_factory=RegistrarConfig)
    initial_domains: list[dict[str, Any]] = Field(default_factory=list)


# ─────────────────────────────────────────────
# PHASE 6: IN-WORLD IDENTITY (L2)
# ─────────────────────────────────────────────


class InWorldUser(SpecModel):
    """User in an org."""

    username: str = Field(...)
    email: str = Field(...)


class OrgUsers(SpecModel):
    """Users for an organization."""

    org: str = Field(...)
    users: list[InWorldUser] = Field(default_factory=list)


class IdentityInWorldPhase(SpecModel):
    """Phase 6: In-world Identity (L2 — inhabitants and users)."""

    oidc_provider: str = Field(default="keycloak")
    listen_ip: str = Field(default="10.0.0.12")
    canonical_name: str = Field(default="auth.internal")
    realm_name: str = Field(default="inworld")
    org_users: list[OrgUsers] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["profile", "email", "openid"])


# ─────────────────────────────────────────────
# PHASE 7: ANDs
# ─────────────────────────────────────────────


class ANDProfileDef(SpecModel):
    """AND profile definition (network behavior spec)."""

    dhcp: bool = Field(default=True)
    nat: bool = Field(default=True)
    dynamic_ip: bool = Field(default=True)
    inbound: str = Field(default="blocked")  # blocked | allowed
    reverse_dns: bool = Field(default=False)
    bgp: Optional[str] = None  # optional | required | disabled


class ANDInstance(SpecModel):
    """Deployed AND (Administrative Network Domain)."""

    name: str = Field(...)
    org: str = Field(...)
    profile: str = Field(...)  # key into ANDsPhase.profiles dict
    dns_suffix: str = Field(...)


class BGPFabricConfig(SpecModel):
    """BGP fabric config (future scope)."""

    enabled: bool = Field(default=False)


class ANDsPhase(SpecModel):
    """Phase 7: ANDs (Administrative Network Domains)."""

    profiles: dict[str, ANDProfileDef] = Field(default_factory=dict)
    instances: list[ANDInstance] = Field(default_factory=list)
    bgp_fabric: BGPFabricConfig = Field(default_factory=BGPFabricConfig)


# ─────────────────────────────────────────────
# PHASE 8: SERVICES
# ─────────────────────────────────────────────


class DKIMConfig(SpecModel):
    """DKIM signing config."""

    enabled: bool = Field(default=True)
    key_signing_policy: Lifecycle = Field(default=Lifecycle.EPHEMERAL)


class DMARCConfig(SpecModel):
    """DMARC policy config."""

    enabled: bool = Field(default=True)
    policy: str = Field(default="reject")


class MailboxPolicy(SpecModel):
    """Mailbox provisioning policy."""

    auto_provision_from_orgs: bool = Field(default=True)
    quota_mb: int = Field(default=1000)
    spf_default: str = Field(default="v=spf1 mx -all")
    dmarc_default: str = Field(default="v=DMARC1; p=reject")


class MailConfig(SpecModel):
    """Mail infrastructure config."""

    enabled: bool = Field(default=True)
    server: str = Field(default="postfix")
    listen_ip: str = Field(default="10.0.0.13")
    canonical_name: str = Field(default="mail.internal")
    dkim: DKIMConfig = Field(default_factory=DKIMConfig)
    dmarc: DMARCConfig = Field(default_factory=DMARCConfig)
    mailbox_policy: MailboxPolicy = Field(default_factory=MailboxPolicy)
    postmaster_address: str = Field(default="postmaster@platform.internal")


class MinIOBucket(SpecModel):
    """MinIO bucket definition."""

    name: str = Field(...)
    description: Optional[str] = None
    scope: str = Field(default="platform")  # platform | org


class StorageConfig(SpecModel):
    """Object storage config."""

    enabled: bool = Field(default=True)
    server: str = Field(default="minio")
    listen_ip: str = Field(default="10.0.0.14")
    canonical_name: str = Field(default="storage.platform.internal")
    access_key_id: str = Field(default="generated")
    buckets: list[MinIOBucket] = Field(default_factory=list)


class WorldServicesPhase(SpecModel):
    """World-level services provisioned at Phase 8."""

    mail: MailConfig = Field(default_factory=MailConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


class AppCatalogEntry(SpecModel):
    """Deployable application catalog entry."""

    name: str = Field(...)
    description: Optional[str] = None
    image: str = Field(...)
    port: int = Field(...)
    oidc_integration: bool = Field(default=False)
    scope: AppScope = Field(default=AppScope.ALL)


class AppDeployment(SpecModel):
    """Deployed org app."""

    org: str = Field(...)
    app: str = Field(...)
    domain: Optional[str] = None
    subdomain: Optional[str] = None


class OrgAppsPhase(SpecModel):
    """Org apps (deployed by orgs into their ANDs)."""

    enabled: bool = Field(default=True)
    catalog: list[AppCatalogEntry] = Field(default_factory=list)
    deployments: list[AppDeployment] = Field(default_factory=list)


# ─────────────────────────────────────────────
# GATEWAY PORTAL
# ─────────────────────────────────────────────


class ServiceMirror(SpecModel):
    """Service mirror for mirrored gateway mode."""

    real_hostname: str = Field(...)
    in_world_service: str = Field(...)


class RealInternetConfig(SpecModel):
    """Real internet connectivity config."""

    mode: GatewayRealInternetMode = Field(default=GatewayRealInternetMode.ISOLATED)
    service_mirrors: list[ServiceMirror] = Field(default_factory=list)
    upstream_resolver_enabled: bool = Field(default=False)
    upstream_resolver_ip: Optional[str] = None


class CrossWorldPeer(SpecModel):
    """Cross-world peer definition."""

    name: str = Field(...)
    endpoint: str = Field(...)
    mode: GatewayCrossWorldMode = Field(default=GatewayCrossWorldMode.PEERED)
    trust_bundle: Optional[str] = None
    trust_anchor_cert: Optional[str] = None


class CrossWorldConfig(SpecModel):
    """Cross-world connectivity config."""

    mode: GatewayCrossWorldMode = Field(default=GatewayCrossWorldMode.NONE)
    peers: list[CrossWorldPeer] = Field(default_factory=list)


class GatewayPortal(SpecModel):
    """Gateway portal (boundary object, not a service)."""

    enabled: bool = Field(default=True)
    real_internet: RealInternetConfig = Field(default_factory=RealInternetConfig)
    cross_world: CrossWorldConfig = Field(default_factory=CrossWorldConfig)


# ─────────────────────────────────────────────
# OPERATOR API
# ─────────────────────────────────────────────


class OperatorAPIConfig(SpecModel):
    """Operator API configuration."""

    enabled: bool = Field(default=True)
    listen_ip: str = Field(default="172.28.0.11")
    port: int = Field(default=8080)
    canonical_name: str = Field(default="api.platform.internal")


class OperatorAuthConfig(SpecModel):
    """Operator API auth config."""

    provider: str = Field(default="oidc")
    issuer: str = Field(default="https://auth.platform.internal/realms/platform")
    required_scope: str = Field(default="netengines:read")


class OperatorConfig(SpecModel):
    """Operator API and auth configuration."""

    api: OperatorAPIConfig = Field(default_factory=OperatorAPIConfig)
    auth: OperatorAuthConfig = Field(default_factory=OperatorAuthConfig)


class AuthorityPosture(SpecModel):
    """Optional top-level authority posture overrides.

    This is intentionally not required by MVP specs. Existing examples derive
    their authority manifest from established top-level sections via
    ``default_authorities_for_spec`` and ``world_manifest_from_spec``.
    """

    authority_model: str = Field(default="default", description="Authority posture profile")
    dns_root_authority: str | None = Field(default=None)
    ca_trust_authority: str | None = Field(default=None)
    platform_identity_issuer: str | None = Field(default=None)
    inworld_identity_issuer: str | None = Field(default=None)
    world_registry_authority: str | None = Field(default=None)
    domain_registry_authority: str | None = Field(default=None)
    numbering_authority: str | None = Field(default=None)
    transit_boundary_authority: str | None = Field(default=None)

# ─────────────────────────────────────────────
# ROOT: NETENGINESPEC
# ─────────────────────────────────────────────


class NetEngineSpec(SpecModel):
    """Complete NetEngine declarative specification (netengines-spec-v0.2).

    Represents a complete world definition with all phases and services.
    Immutable after parsing.
    """

    metadata: SpecMetadata = Field(..., description="Spec metadata")
    substrate: SubstratePhase = Field(..., description="Phase 0")
    dns: DNSPhase = Field(..., description="Phases 1-2")
    pki: PKIPhase = Field(..., description="Phase 3")
    identity_platform: IdentityPlatformPhase = Field(..., description="Phase 4")
    world_registry: WorldRegistryPhase = Field(..., description="Phase 5a")
    domain_registry: DomainRegistryPhase = Field(..., description="Phase 5b")
    identity_inworld: IdentityInWorldPhase = Field(..., description="Phase 6")
    ands: ANDsPhase = Field(..., description="Phase 7")
    world_services: WorldServicesPhase = Field(..., description="Phase 8 — world services")
    org_apps: OrgAppsPhase = Field(..., description="Phase 9 — org apps")
    gateway_portal: GatewayPortal = Field(..., description="Gateway boundary")
    operator: OperatorConfig = Field(..., description="Operator API")
    authority: AuthorityPosture | None = Field(
        default=None,
        description="Optional authority posture overrides; omitted specs derive defaults",
    )
