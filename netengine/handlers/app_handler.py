from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from netengine.core.pgmq_client import PGMQClient
from netengine.errors import ServicesError
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.handlers.protocols import DockerAdapterProtocol, PGMQAdapterProtocol
from netengine.logs import get_logger
from netengine.spec.models import NetEngineSpec


@dataclass(frozen=True)
class AppOIDCSettings:
    enabled: bool = True
    public: bool = True
    realm: str | None = None
    client_id_template: str = "{org}-{app}"
    redirect_uri_template: str = "https://{domain}/*"


@dataclass(frozen=True)
class AppDNSSettings:
    enabled: bool = True
    ttl: int = 300
    zone_template: str = "{org}.internal"
    domain_template: str = "{subdomain}.{org}.internal"


@dataclass(frozen=True)
class AppCatalogEntry:
    name: str
    image: str
    ports: list[int] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, Any] = field(default_factory=dict)
    healthcheck_path: str = "/"
    oidc: AppOIDCSettings = field(default_factory=AppOIDCSettings)
    dns: AppDNSSettings = field(default_factory=AppDNSSettings)


class AppCatalog:
    """Catalog of deployable org applications with runtime deployment metadata."""

    _BUILTINS: dict[str, AppCatalogEntry] = {
        "gitea": AppCatalogEntry("gitea", "gitea/gitea:latest", [3000], healthcheck_path="/"),
        "wordpress": AppCatalogEntry("wordpress", "wordpress:latest", [80], healthcheck_path="/"),
        "nextcloud": AppCatalogEntry(
            "nextcloud", "nextcloud:latest", [80], healthcheck_path="/status.php"
        ),
    }

    def __init__(self, entries: list[Any] | None = None) -> None:
        self._entries = dict(self._BUILTINS)
        for entry in entries or []:
            parsed = self._from_spec(entry)
            self._entries[parsed.name] = parsed

    def get(self, app_name: str) -> AppCatalogEntry:
        return self._entries.get(app_name, AppCatalogEntry(app_name, app_name, []))

    def _from_spec(self, entry: Any) -> AppCatalogEntry:
        data = entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
        oidc_raw = data.get("oidc") or data.get("oidc_settings") or {}
        dns_raw = data.get("dns") or data.get("dns_settings") or {}
        return AppCatalogEntry(
            name=data["name"],
            image=data["image"],
            ports=list(data.get("ports") or ([data["port"]] if data.get("port") else [])),
            environment=dict(data.get("environment") or data.get("env") or {}),
            volumes=dict(data.get("volumes") or {}),
            healthcheck_path=data.get("healthcheck_path") or data.get("healthcheck") or "/",
            oidc=AppOIDCSettings(
                enabled=bool(data.get("oidc_integration", oidc_raw.get("enabled", True))),
                public=bool(oidc_raw.get("public", True)),
                realm=oidc_raw.get("realm"),
                client_id_template=oidc_raw.get("client_id_template", "{org}-{app}"),
                redirect_uri_template=oidc_raw.get("redirect_uri_template", "https://{domain}/*"),
            ),
            dns=AppDNSSettings(
                enabled=bool(dns_raw.get("enabled", True)),
                ttl=int(dns_raw.get("ttl", 300)),
                zone_template=dns_raw.get("zone_template", "{org}.internal"),
                domain_template=dns_raw.get("domain_template", "{subdomain}.{org}.internal"),
            ),
        )


class AppHandler:
    def __init__(
        self,
        docker: DockerAdapterProtocol,
        dns: DNSHandler,
        pki: PKIHandler,
        oidc: OIDCHandler,
        state: Any,
        context: PhaseContext | None = None,
        pgmq: PGMQAdapterProtocol | None = None,
        catalog: AppCatalog | None = None,
    ) -> None:
        self.context = context or PhaseContext(
            spec=cast(NetEngineSpec, {}), runtime_state=state, logger=get_logger(__name__)
        )
        self.docker = docker
        self.dns = dns
        self.pki = pki
        self.oidc = oidc
        self.state = state
        self.catalog = catalog or AppCatalog(
            getattr(getattr(self.context, "spec", None), "org_apps", None)
            and getattr(self.context.spec.org_apps, "catalog", [])
        )
        self._db = None
        self.pgmq = pgmq or self.context.pgmq_client or PGMQClient()

    async def _get_db(self) -> Any:
        if self._db is None:
            from netengine.core.supabase_client import get_db

            self._db = await get_db()
        return self._db

    async def deploy_app(
        self, org: str, app_name: str, subdomain: str, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = config or {}
        entry = self.catalog.get(app_name)
        now = datetime.now(UTC).isoformat()
        container_name = f"netengines_{org}_{app_name}"
        domain = entry.dns.domain_template.format(org=org, app=app_name, subdomain=subdomain)
        client_id = entry.oidc.client_id_template.format(org=org, app=app_name, domain=domain)
        deployment = {
            "org": org,
            "app": app_name,
            "domain": domain,
            "container_id": None,
            "client_id": client_id if entry.oidc.enabled else None,
            "status": "deploying",
            "failure_detail": None,
            "deployed_at": now,
            "updated_at": now,
        }
        await self._persist_deployment(deployment)
        try:
            cert, key = await self.pki.issue_cert(domain, [f"*.{org}.internal"])
            cert_mount = await self._prepare_cert_mount(container_name, domain, cert, key)
            and_name = f"{org.replace('_', '-')}-net"
            container_id = await self._start_app_container(
                container_name, entry, and_name, config, cert_mount
            )
            deployment["container_id"] = container_id
            if entry.dns.enabled:
                gateway_ip = await self._get_gateway_ip(and_name)
                zone = entry.dns.zone_template.format(org=org, app=app_name, subdomain=subdomain)
                await self.dns.add_zone_record(
                    self.context, zone, "A", subdomain, gateway_ip, entry.dns.ttl
                )
            self._record_certificate(domain, org, cert)
            if entry.oidc.enabled:
                await self.oidc.create_client(
                    realm=self._oidc_realm(org, entry),
                    client_id=client_id,
                    name=f"{org} {app_name}",
                    redirect_uris=[
                        entry.oidc.redirect_uri_template.format(
                            domain=domain, org=org, app=app_name
                        )
                    ],
                    public=entry.oidc.public,
                )
            deployment.update({"status": "deployed", "updated_at": datetime.now(UTC).isoformat()})
            await self._persist_deployment(deployment)
            return deployment
        except Exception as exc:
            deployment.update(
                {
                    "status": "failed",
                    "failure_detail": str(exc),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            await self._persist_deployment(deployment)
            raise

    async def _persist_deployment(self, deployment: dict[str, Any]) -> None:
        db = await self._get_db()
        await db.table("app_deployments").upsert(dict(deployment)).execute()

    def _record_certificate(self, domain: str, org: str, cert: str) -> None:
        expiry = self.pki.extract_cert_expiry(cert)
        self.context.runtime_state.issued_certificates[domain] = {
            "cert_type": "app",
            "issued_at": datetime.now(UTC).isoformat(),
            "expires_at": expiry.isoformat(),
            "sans": [f"*.{org}.internal"],
            "rotated_at": None,
            "version": 1,
        }
        self.context.runtime_state.save()

    async def _prepare_cert_mount(
        self, container_name: str, domain: str, cert: str, key: str
    ) -> dict[str, Any]:
        cert_dir = (
            Path(os.environ.get("NETENGINE_APP_CERT_DIR", "/var/lib/netengines/certs"))
            / container_name
        )
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "tls.crt").write_text(cert)
        (cert_dir / "tls.key").write_text(key)
        return {str(cert_dir): {"bind": "/certs", "mode": "ro"}}

    async def _start_app_container(
        self,
        name: str,
        entry: AppCatalogEntry,
        network: str,
        config: dict[str, Any],
        cert_mount: dict[str, Any],
    ) -> str:
        volumes = {**entry.volumes, **config.get("volumes", {}), **cert_mount}
        env = {**entry.environment, **config.get("environment", {})}
        container_id = await self.docker.start_container(
            name=name,
            image=entry.image,
            command=config.get("command", []),
            volumes=volumes,
            network=None,
            ip=None,
            environment=env,
            ports=entry.ports,
            healthcheck_path=entry.healthcheck_path,
        )
        await self.docker.connect_network(container_id, f"netengines_and_{network}", ip=None)
        return container_id

    def _oidc_realm(self, org: str, entry: AppCatalogEntry) -> str:
        if entry.oidc.realm:
            return entry.oidc.realm.format(org=org)
        output = self.context.runtime_state.identity_inworld_output or {}
        if "realm_name" in output:
            return str(output["realm_name"])
        realms = output.get("realms_created") or []
        org_realm = f"{org}-realm"
        return (
            org_realm
            if org_realm in realms
            else getattr(self.context.spec.identity_inworld, "realm_name", "inworld")
        )

    async def update_app(
        self, deployment: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self.deploy_app(
            deployment["org"], deployment["app"], deployment["domain"].split(".")[0], config or {}
        )

    async def restart_app(self, container_id: str) -> None:
        await self.docker.stop_container(container_id)
        await self.docker.start_container(
            name=container_id,
            image="",
            command=[],
            volumes={},
            network=None,
            ip=None,
            environment={},
        )

    async def teardown_app(self, deployment: dict[str, Any]) -> None:
        if deployment.get("container_id"):
            await self.docker.stop_container(deployment["container_id"])
        deployment.update({"status": "torn_down", "updated_at": datetime.now(UTC).isoformat()})
        await self._persist_deployment(deployment)

    async def healthcheck_app(self, container_id: str, path: str = "/") -> bool:
        code, _ = await self.docker.exec_command(
            container_id, ["wget", "-q", "-O", "-", f"http://127.0.0.1{path}"]
        )
        return code == 0

    async def _get_gateway_ip(self, and_name: str) -> str:
        import ipaddress

        db = await self._get_db()
        result = await db.table("address_leases").select("cidr").eq("and_name", and_name).execute()
        if not result.data:
            raise ServicesError(f"AND {and_name} not found")
        return str(ipaddress.ip_network(result.data[0]["cidr"], strict=False).network_address + 1)


class OrgAppsPhaseHandler(BasePhaseHandler):
    """Phase 9: Deploy org apps declared in spec.org_apps.deployments."""

    async def execute(self, context: PhaseContext) -> None:
        org_apps_spec = getattr(context.spec, "org_apps", None)
        if not org_apps_spec or not org_apps_spec.enabled:
            context.logger.info("Phase 9: org_apps not enabled, skipping deployments")
            context.runtime_state.org_apps_output = cast(Any, {"deployments": []})
            return
        docker = context.docker_client
        if docker is None:
            raise ServicesError("docker_client is required for org app deployments")
        inworld_spec = context.spec.identity_inworld
        identity_output = context.runtime_state.identity_inworld_output or {}
        keycloak_url = (
            identity_output.get("keycloak_url")
            or identity_output.get("issuer", "").split("/realms/")[0]
            or f"https://{inworld_spec.canonical_name}"
        )
        admin_password = (
            identity_output.get("admin_password")
            or context.runtime_state.inworld_admin_password
            or ""
        )
        oidc = OIDCHandler(
            keycloak_url=keycloak_url,
            admin_username=identity_output.get("admin_username", "admin"),
            admin_password=admin_password,
        )
        handler = AppHandler(
            docker,
            DNSHandler(),
            PKIHandler(docker, context.runtime_state, context.spec),
            oidc,
            context.runtime_state,
            context,
            catalog=AppCatalog(org_apps_spec.catalog),
        )
        deployments = []
        for dep in org_apps_spec.deployments:
            subdomain = dep.subdomain or dep.app
            result = await handler.deploy_app(dep.org, dep.app, subdomain, {})
            deployments.append(result)
            context.logger.info(f"Phase 9: deployed {dep.app} for {dep.org} at {result['domain']}")
        context.runtime_state.org_apps_output = cast(Any, {"deployments": deployments})

    async def healthcheck(self, context: PhaseContext) -> bool:
        return bool(context.runtime_state.org_apps_output)

    async def should_skip(self, context: PhaseContext) -> bool:
        return bool(context.runtime_state.org_apps_output)
