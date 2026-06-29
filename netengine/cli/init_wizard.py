"""Interactive wizard for `netengine init` — builds a WorldConfig from CLI prompts."""

import ipaddress
from dataclasses import dataclass, field
from typing import Optional

import click
import yaml

# ── helpers ───────────────────────────────────────────────────────────────────


def _p(prompt: str, default: str, yes: bool, **kwargs: object) -> str:
    if yes:
        return default
    return str(click.prompt(prompt, default=default, **kwargs))


def _confirm(prompt: str, default: bool, yes: bool) -> bool:
    if yes:
        return default
    return bool(click.confirm(prompt, default=default))


def _choice(prompt: str, options: list[str], default: str, yes: bool) -> str:
    if yes:
        return default
    return str(click.prompt(prompt, type=click.Choice(options), default=default))


def _header(text: str) -> None:
    click.echo(
        "\n"
        + click.style(f"── {text} ", fg="cyan")
        + click.style("─" * (50 - len(text)), fg="bright_black")
    )


# ── data model ────────────────────────────────────────────────────────────────


@dataclass
class OrgConfig:
    name: str
    description: str = ""
    and_profile: str = "business"
    capabilities: list[str] = field(default_factory=lambda: ["host_services"])
    users: list[dict[str, str]] = field(default_factory=list)


@dataclass
class WorldConfig:
    # Identity
    name: str
    lifecycle: str = "ephemeral"
    owner_org: Optional[str] = None
    environment: Optional[str] = None

    # Network
    platform_subnet: str = "172.28.0.0/16"
    core_subnet: str = "10.0.0.0/24"
    internet_mode: str = "isolated"

    # PKI
    ca_cn: str = ""
    ca_o: str = ""
    ca_c: str = "US"
    cert_lifetime_days: int = 3650
    crl_enabled: bool = False
    ocsp_enabled: bool = False
    intermediate_ca: bool = False

    # Admin
    admin_username: str = "admin"
    admin_email: str = "admin@platform.internal"

    # Orgs / ANDs
    orgs: list[OrgConfig] = field(default_factory=list)
    extra_tlds: list[str] = field(default_factory=list)

    # Services
    mail_enabled: bool = False
    storage_enabled: bool = False
    mail_quota_mb: int = 1000
    dmarc_policy: str = "reject"
    storage_buckets: list[str] = field(default_factory=lambda: ["platform"])

    # Org apps
    gitea_enabled: bool = False
    mailpit_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.ca_cn:
            self.ca_cn = f"{self.name} Root CA"
        if not self.ca_o:
            self.ca_o = self.name


# ── wizard sections ───────────────────────────────────────────────────────────


def _wizard_world_identity(
    yes: bool,
    default_name: str = "my-world",
    default_lifecycle: str = "ephemeral",
) -> tuple[str, str, Optional[str], Optional[str]]:
    _header("World Identity")
    name = _p("World name", default_name, yes)
    lifecycle = _choice("Lifecycle", ["ephemeral", "persistent"], default_lifecycle, yes)
    owner_org: Optional[str] = None
    environment: Optional[str] = None
    if not yes:
        raw_owner = click.prompt("Owner organisation (optional — press Enter to skip)", default="")
        owner_org = raw_owner.strip() or None
        raw_env = click.prompt("Environment label (dev/staging/prod — optional)", default="")
        environment = raw_env.strip() or None
    return name, lifecycle, owner_org, environment


def _wizard_network(name: str, yes: bool) -> tuple[str, str, str]:
    _header("Network")
    click.echo(
        "  Platform subnet: management traffic (operator API, Keycloak, etc.)\n"
        "  Core subnet:     in-world traffic (DNS, PKI, services)\n"
        "  Avoid RFC-1918 ranges already in use on your Docker host.\n"
    )
    platform_subnet = _p("Platform subnet (CIDR)", "172.28.0.0/16", yes)
    core_subnet = _p("Core subnet (CIDR)", "10.0.0.0/24", yes)
    internet_mode = _choice(
        "Real-internet mode",
        ["isolated", "shadowed", "mirrored", "exposed"],
        "isolated",
        yes,
    )
    # Validate CIDRs
    for label, cidr in [("Platform subnet", platform_subnet), ("Core subnet", core_subnet)]:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint=label) from exc
    return platform_subnet, core_subnet, internet_mode


def _wizard_pki(name: str, yes: bool) -> tuple[str, str, str, int, bool, bool, bool]:
    _header("PKI — Certificate Authority")
    ca_cn = _p("CA common name", f"{name} Root CA", yes)
    ca_o = _p("CA organisation", name, yes)
    ca_c = _p("CA country code (2-letter)", "US", yes)
    cert_lifetime_days = int(_p("Root cert lifetime (days)", "3650", yes))
    crl_enabled = _confirm("Enable CRL endpoint?", False, yes)
    ocsp_enabled = _confirm("Enable OCSP endpoint?", False, yes)
    intermediate_ca = _confirm("Enable intermediate CA?", False, yes)
    return ca_cn, ca_o, ca_c, cert_lifetime_days, crl_enabled, ocsp_enabled, intermediate_ca


def _wizard_admin(name: str, yes: bool) -> tuple[str, str]:
    _header("Platform Administrator")
    admin_username = _p("Admin username", "admin", yes)
    admin_email = _p("Admin email", f"admin@{name}.internal", yes)
    return admin_username, admin_email


_AND_PROFILE_DESCRIPTIONS = {
    "business": "static IPs, inbound allowed, reverse DNS",
    "residential": "DHCP, NAT, inbound blocked",
    "datacenter": "static IPs, inbound allowed, no NAT",
    "airgapped": "no external routing, fully isolated",
}

_CAPABILITY_OPTIONS = ["host_services", "send_mail", "register_domains"]


def _wizard_one_org(index: int, yes: bool, first_tld: str) -> OrgConfig:
    click.echo(f"\n  Organisation {index}:")
    org_name = _p("    Name", f"org-{index}", yes)
    description = "" if yes else (click.prompt("    Description (optional)", default="") or "")
    click.echo("    AND profiles:")
    for p, desc in _AND_PROFILE_DESCRIPTIONS.items():
        click.echo(f"      {p:<12} — {desc}")
    and_profile = _choice("    AND profile", list(_AND_PROFILE_DESCRIPTIONS), "business", yes)

    if yes:
        capabilities = ["host_services"]
    else:
        click.echo(f"    Capabilities ({', '.join(_CAPABILITY_OPTIONS)}):")
        raw = click.prompt("    Grant capabilities (comma-separated)", default="host_services")
        capabilities = [c.strip() for c in raw.split(",") if c.strip() in _CAPABILITY_OPTIONS]
        if not capabilities:
            capabilities = ["host_services"]

    # Users
    users: list[dict[str, str]] = []
    if not yes:
        while click.confirm(f"    Add a user to {org_name}?", default=False):
            u_name = click.prompt("      Username")
            slug = org_name.lower().replace(" ", "-")
            u_email = click.prompt("      Email", default=f"{u_name}@{slug}.{first_tld}")
            users.append({"username": u_name, "email": u_email})

    return OrgConfig(
        name=org_name,
        description=description,
        and_profile=and_profile,
        capabilities=capabilities,
        users=users,
    )


def _wizard_orgs(yes: bool, tlds: list[str]) -> tuple[list[OrgConfig], list[str]]:
    _header("Organisations & ANDs")
    extra_tlds: list[str] = []
    if not yes:
        raw_tlds = click.prompt(
            "Extra TLDs beyond 'internal' (comma-separated, or Enter to skip)", default=""
        )
        extra_tlds = [t.strip() for t in raw_tlds.split(",") if t.strip()]

    first_tld = extra_tlds[0] if extra_tlds else "internal"

    orgs: list[OrgConfig] = []
    if yes:
        return orgs, extra_tlds

    while click.confirm(f"Add an organisation?", default=bool(not orgs)):
        org = _wizard_one_org(len(orgs) + 1, yes, first_tld)
        orgs.append(org)

    return orgs, extra_tlds


def _wizard_services(yes: bool) -> tuple[bool, int, str, bool, list[str]]:
    _header("World Services")
    mail_enabled = _confirm("Enable mail (Postfix + DKIM/DMARC)?", False, yes)
    mail_quota_mb = 1000
    dmarc_policy = "reject"
    if mail_enabled and not yes:
        mail_quota_mb = int(click.prompt("  Mailbox quota (MB)", default="1000"))
        dmarc_policy = click.prompt(
            "  DMARC policy", type=click.Choice(["none", "quarantine", "reject"]), default="reject"
        )

    storage_enabled = _confirm("Enable object storage (MinIO)?", False, yes)
    buckets = ["platform"]
    if storage_enabled and not yes:
        raw = click.prompt("  Bucket names (comma-separated)", default="platform")
        buckets = [b.strip() for b in raw.split(",") if b.strip()] or ["platform"]

    return mail_enabled, mail_quota_mb, dmarc_policy, storage_enabled, buckets


def _wizard_apps(yes: bool) -> tuple[bool, bool]:
    _header("Org App Catalog")
    gitea = _confirm("Add Gitea (self-hosted git)?", False, yes)
    mailpit = _confirm("Add Mailpit (dev mail sink)?", False, yes)
    return gitea, mailpit


# ── preset factories ──────────────────────────────────────────────────────────


def _preset_minimal(name: str, lifecycle: str) -> WorldConfig:
    return WorldConfig(name=name, lifecycle=lifecycle)


def _preset_single_org(name: str, lifecycle: str, yes: bool) -> WorldConfig:
    cfg = WorldConfig(name=name, lifecycle=lifecycle, mail_enabled=True, storage_enabled=True)
    first_tld = "internal"
    org = _wizard_one_org(1, yes, first_tld)
    cfg.orgs = [org]
    cfg.gitea_enabled = True
    return cfg


def _preset_dev_sandbox(name: str, lifecycle: str) -> WorldConfig:
    return WorldConfig(
        name=name,
        lifecycle=lifecycle,
        environment="development",
        extra_tlds=["localnet"],
        mail_enabled=True,
        storage_enabled=True,
        gitea_enabled=True,
        mailpit_enabled=True,
        orgs=[
            OrgConfig(
                name="acme-corp",
                description="Acme Corporation",
                and_profile="business",
                capabilities=["host_services", "send_mail", "register_domains"],
                users=[
                    {"username": "alice", "email": "alice@acme.internal"},
                    {"username": "bob", "email": "bob@acme.internal"},
                ],
            ),
            OrgConfig(
                name="bob-home",
                description="Bob's home network",
                and_profile="residential",
                capabilities=["send_mail"],
                users=[{"username": "bob", "email": "bob@bob-home.localnet"}],
            ),
        ],
    )


# ── full wizard entry point ───────────────────────────────────────────────────


def run_wizard(
    preset: Optional[str],
    yes: bool,
    name: Optional[str] = None,
    lifecycle: Optional[str] = None,
) -> WorldConfig:
    """Drive the wizard and return a populated WorldConfig."""
    _header("NetEngine World Setup")

    wiz_name, wiz_lifecycle, owner_org, environment = _wizard_world_identity(
        yes, default_name=name or "my-world", default_lifecycle=lifecycle or "ephemeral"
    )

    name = wiz_name
    lifecycle = wiz_lifecycle

    if preset == "minimal":
        cfg = _preset_minimal(name, lifecycle)
        cfg.owner_org = owner_org
        cfg.environment = environment
        return cfg

    if preset == "single-org":
        cfg = _preset_single_org(name, lifecycle, yes)
        cfg.owner_org = owner_org
        cfg.environment = environment
        return cfg

    if preset == "dev-sandbox":
        cfg = _preset_dev_sandbox(name, lifecycle)
        cfg.owner_org = owner_org
        cfg.environment = environment
        return cfg

    # custom / no preset — full wizard
    if not yes and preset is None:
        mode = click.prompt(
            "\nSetup mode",
            type=click.Choice(["minimal", "single-org", "dev-sandbox", "custom"]),
            default="minimal",
        )
        if mode == "minimal":
            cfg = _preset_minimal(name, lifecycle)
            cfg.owner_org = owner_org
            cfg.environment = environment
            return cfg
        if mode == "single-org":
            cfg = _preset_single_org(name, lifecycle, yes)
            cfg.owner_org = owner_org
            cfg.environment = environment
            return cfg
        if mode == "dev-sandbox":
            cfg = _preset_dev_sandbox(name, lifecycle)
            cfg.owner_org = owner_org
            cfg.environment = environment
            return cfg

    # custom path
    platform_subnet, core_subnet, internet_mode = _wizard_network(name, yes)
    ca_cn, ca_o, ca_c, cert_lifetime, crl, ocsp, intermediate = _wizard_pki(name, yes)
    admin_user, admin_email = _wizard_admin(name, yes)
    orgs, extra_tlds = _wizard_orgs(yes, [])
    mail_enabled, mail_quota, dmarc, storage_enabled, buckets = _wizard_services(yes)
    gitea, mailpit = _wizard_apps(yes)

    return WorldConfig(
        name=name,
        lifecycle=lifecycle,
        owner_org=owner_org,
        environment=environment,
        platform_subnet=platform_subnet,
        core_subnet=core_subnet,
        internet_mode=internet_mode,
        ca_cn=ca_cn,
        ca_o=ca_o,
        ca_c=ca_c,
        cert_lifetime_days=cert_lifetime,
        crl_enabled=crl,
        ocsp_enabled=ocsp,
        intermediate_ca=intermediate,
        admin_username=admin_user,
        admin_email=admin_email,
        orgs=orgs,
        extra_tlds=extra_tlds,
        mail_enabled=mail_enabled,
        storage_enabled=storage_enabled,
        mail_quota_mb=mail_quota,
        dmarc_policy=dmarc,
        storage_buckets=buckets,
        gitea_enabled=gitea,
        mailpit_enabled=mailpit,
    )


# ── spec builder ──────────────────────────────────────────────────────────────


def _core_host(host: int, subnet: str) -> str:
    net = ipaddress.ip_network(subnet, strict=False)
    return str(net.network_address + host)


_AND_PROFILE_DEFS: dict[str, dict[str, object]] = {
    "business": {
        "dhcp": True,
        "nat": False,
        "dynamic_ip": False,
        "inbound": "allowed",
        "reverse_dns": False,
    },
    "residential": {
        "dhcp": True,
        "nat": True,
        "dynamic_ip": True,
        "inbound": "blocked",
        "reverse_dns": False,
    },
    "datacenter": {
        "dhcp": False,
        "nat": False,
        "dynamic_ip": False,
        "inbound": "allowed",
        "reverse_dns": False,
    },
    "airgapped": {
        "dhcp": True,
        "nat": False,
        "dynamic_ip": False,
        "inbound": "blocked",
        "reverse_dns": False,
    },
}


def build_spec_dict(cfg: WorldConfig) -> dict[str, object]:
    """Convert a WorldConfig into the full spec dictionary."""

    def core(n: int) -> str:
        return _core_host(n, cfg.core_subnet)

    plat_net = ipaddress.ip_network(cfg.platform_subnet, strict=False)
    platform_gw = str(plat_net.network_address + 1)
    operator_api_ip = str(plat_net.network_address + 11)

    # TLDs
    tlds: list[dict[str, object]] = [
        {
            "name": "internal",
            "description": "Default in-world TLD",
            "type": "authoritative",
            "listen_ip": core(4),
        }
    ]
    for i, tld_name in enumerate(cfg.extra_tlds):
        tlds.append({"name": tld_name, "type": "authoritative", "listen_ip": core(5 + i)})

    tld_delegations = [{"tld": str(t["name"]), "governed_by": "platform"} for t in tlds]

    first_tld = cfg.extra_tlds[0] if cfg.extra_tlds else "internal"

    # Organisations
    organizations: list[dict[str, object]] = []
    for org in cfg.orgs:
        entry: dict[str, object] = {"name": org.name, "and_profile": org.and_profile}
        if org.description:
            entry["description"] = org.description
        if org.capabilities:
            entry["capabilities"] = org.capabilities
        organizations.append(entry)

    # AND profiles and instances
    profiles_needed = {org.and_profile for org in cfg.orgs}
    and_profiles = {p: _AND_PROFILE_DEFS[p] for p in profiles_needed if p in _AND_PROFILE_DEFS}

    and_instances: list[dict[str, object]] = []
    for org in cfg.orgs:
        slug = org.name.lower().replace(" ", "-")
        and_instances.append(
            {
                "name": f"{slug}-net",
                "org": org.name,
                "profile": org.and_profile,
                "dns_suffix": f"{slug}.{first_tld}",
            }
        )

    # In-world users
    org_users: list[dict[str, object]] = [
        {"org": org.name, "users": org.users} for org in cfg.orgs if org.users
    ]

    # Address pools (only when orgs exist)
    address_space: list[dict[str, object]] = (
        [
            {
                "cidr": "192.168.0.0/16",
                "label": "residential-pool",
                "allocated_to": "residential-ands",
            },
            {"cidr": "10.100.0.0/16", "label": "business-pool", "allocated_to": "business-ands"},
            {
                "cidr": "10.200.0.0/16",
                "label": "datacenter-pool",
                "allocated_to": "datacenter-ands",
            },
        ]
        if cfg.orgs
        else []
    )

    # Mail
    mail_cfg: dict[str, object] = {"enabled": cfg.mail_enabled}
    if cfg.mail_enabled:
        mail_cfg.update(
            {
                "server": "postfix",
                "listen_ip": core(13),
                "canonical_name": "mail.internal",
                "dkim": {"enabled": True, "key_signing_policy": "ephemeral"},
                "dmarc": {"enabled": True, "policy": cfg.dmarc_policy},
                "mailbox_policy": {
                    "auto_provision_from_orgs": True,
                    "quota_mb": cfg.mail_quota_mb,
                },
            }
        )

    # Storage
    storage_cfg: dict[str, object] = {"enabled": cfg.storage_enabled}
    if cfg.storage_enabled:
        storage_cfg.update(
            {
                "server": "minio",
                "listen_ip": core(14),
                "canonical_name": "storage.platform.internal",
                "buckets": [
                    {"name": b, "description": f"{b.capitalize()} bucket", "scope": "platform"}
                    for b in cfg.storage_buckets
                ],
            }
        )

    # App catalog
    catalog: list[dict[str, object]] = []
    if cfg.gitea_enabled:
        catalog.append(
            {
                "name": "gitea",
                "description": "Self-hosted git service",
                "image": "gitea/gitea:latest",
                "port": 3000,
                "oidc_integration": True,
            }
        )
    if cfg.mailpit_enabled:
        catalog.append(
            {
                "name": "mailpit",
                "description": "Dev mail sink",
                "image": "axllent/mailpit:latest",
                "port": 8025,
                "scope": "dev_only",
            }
        )

    metadata: dict[str, object] = {"name": cfg.name, "version": "1.0", "lifecycle": cfg.lifecycle}
    if cfg.owner_org:
        metadata["organization"] = cfg.owner_org
    if cfg.environment:
        metadata["environment"] = cfg.environment

    return {
        "metadata": metadata,
        "substrate": {
            "orchestrator": "swarm",
            "ntp": {"enabled": True, "servers": ["pool.ntp.org"]},
            "networks": {
                "platform": {
                    "type": "bridge",
                    "subnet": cfg.platform_subnet,
                    "description": "Platform management network",
                },
                "core": {
                    "type": "bridge",
                    "subnet": cfg.core_subnet,
                    "description": "In-world core network",
                },
            },
            "gateway": {
                "platform_ip": platform_gw,
                "core_ip": core(1),
                "description": "Gateway stub",
            },
        },
        "dns": {
            "root": {
                "enabled": True,
                "type": "authoritative",
                "server": "coredns",
                "listen_ip": core(2),
                "soa_primary_ns": "root.internal",
                "soa_email": "admin.internal",
                "serial_policy": "timestamp",
            },
            "platform_zone": {
                "name": "platform.internal",
                "type": "authoritative",
                "listen_ip": core(3),
            },
            "tlds": tlds,
        },
        "pki": {
            "root_ca": {
                "cn": cfg.ca_cn,
                "o": cfg.ca_o,
                "c": cfg.ca_c,
                "key_storage_mode": cfg.lifecycle,
                "cert_lifetime_days": cfg.cert_lifetime_days,
            },
            "acme": {
                "enabled": True,
                "listen_ip": core(6),
                "canonical_name": "ca.platform.internal",
            },
            "intermediate_ca_enabled": cfg.intermediate_ca,
            "dnssec_enabled": False,
            "dnssec_ksk_lifetime_days": 365,
            "dnssec_zsk_lifetime_days": 30,
            "crl_enabled": cfg.crl_enabled,
            "ocsp_enabled": cfg.ocsp_enabled,
        },
        "identity_platform": {
            "oidc_provider": "keycloak",
            "listen_ip": core(7),
            "canonical_name": "auth.platform.internal",
            "realm_name": "platform",
            "admin_user": {"username": cfg.admin_username, "email": cfg.admin_email},
            "scopes": ["netengines:read", "netengines:write", "netengines:admin"],
        },
        "world_registry": {
            "enabled": True,
            "listen_ip": core(8),
            "canonical_name": "registry.platform.internal",
            "organizations": organizations,
            "operators": [{"username": cfg.admin_username, "role": "superadmin"}],
            "whois": {"enabled": True, "listen_ip": core(9), "port": 43},
        },
        "domain_registry": {
            "enabled": True,
            "listen_ip": core(10),
            "canonical_name": "domainreg.platform.internal",
            "tld_delegations": tld_delegations,
            "address_space": address_space,
            "registrar": {
                "enabled": True,
                "listen_ip": core(11),
                "canonical_name": "registrar.platform.internal",
            },
        },
        "identity_inworld": {
            "oidc_provider": "keycloak",
            "listen_ip": core(12),
            "canonical_name": "auth.internal",
            "realm_name": "inworld",
            "org_users": org_users,
            "scopes": ["profile", "email", "openid"],
        },
        "ands": {
            "profiles": and_profiles,
            "instances": and_instances,
        },
        "world_services": {
            "mail": mail_cfg,
            "storage": storage_cfg,
        },
        "org_apps": {
            "enabled": True,
            "catalog": catalog,
            "deployments": [],
        },
        "gateway_portal": {
            "enabled": True,
            "real_internet": {"mode": cfg.internet_mode},
            "cross_world": {"mode": "none"},
        },
        "operator": {
            "api": {
                "enabled": True,
                "listen_ip": operator_api_ip,
                "port": 8080,
                "canonical_name": "api.platform.internal",
            },
            "auth": {
                "provider": "oidc",
                "issuer": "https://auth.platform.internal/realms/platform",
                "required_scope": "netengines:read",
            },
        },
    }


def build_spec_yaml(cfg: WorldConfig) -> str:
    spec = build_spec_dict(cfg)
    return yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)
