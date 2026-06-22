import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict

from netengine.core.pgmq_client import PGMQClient
from netengine.core.supabase_client import get_supabase
from netengine.events.schema import EventEnvelope
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler


class AppHandler:
    def __init__(
        self,
        docker: DockerHandler,
        dns: DNSHandler,
        pki: PKIHandler,
        oidc: OIDCHandler,
        state,
        context: PhaseContext | None = None,
    ):
        self.context = context or PhaseContext(
            spec={}, runtime_state=state, logger=logging.getLogger(__name__)
        )
        self.docker = docker
        self.dns = dns
        self.pki = pki
        self.oidc = oidc
        self.state = state
        self.supabase = get_supabase()
        self.pgmq = PGMQClient()

    async def deploy_app(
        self, org: str, app_name: str, subdomain: str, config: Dict[str, Any] = None
    ) -> dict:
        """4‑step deployment: container → DNS → cert → OIDC."""
        # Step 1: Determine AND bridge for this org
        and_name = f"{org.replace('_', '-')}-net"
        # Step 2: Start container inside the org's AND bridge
        container_name = f"netengines_{org}_{app_name}"
        # Use the appropriate image from catalog
        image = self._get_app_image(app_name)
        # We need to attach to the AND bridge.
        # Start container on the AND bridge (not core).
        container_id = await self._start_app_container(container_name, image, and_name, config)

        # Step 3: Register DNS (e.g., gitea.acme.internal)
        domain = f"{subdomain}.{org}.internal"
        # Get gateway IP for this AND (from address_leases)
        gateway_ip = await self._get_gateway_ip(and_name)
        await self.dns.add_zone_record(
            self.context, f"{org}.internal", "A", subdomain, gateway_ip, 300
        )

        # Step 4: Issue TLS certificate via PKI
        cert, key = await self.pki.issue_cert(domain, [f"*.{org}.internal"])
        # Mount cert into container (via volume or exec write)
        await self._inject_cert(container_id, domain, cert, key)

        # Step 5: Create OIDC client in in‑world Keycloak realm
        client_id = f"{org}-{app_name}"
        await self.oidc.create_client(
            realm="inworld",
            client_id=client_id,
            name=f"{org} {app_name}",
            redirect_uris=[f"https://{domain}/*"],
            public=True,
        )

        # Store deployment metadata
        deployment = {
            "org": org,
            "app": app_name,
            "domain": domain,
            "container_id": container_id,
            "client_id": client_id,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
        }
        # Store in Supabase (optional table: app_deployments)
        await self.supabase.table("app_deployments").upsert(deployment).execute()

        return deployment

    async def _start_app_container(self, name: str, image: str, network: str, config: dict) -> str:
        """Start a container on the given AND bridge."""
        # The container should not be attached to core – only to its AND.
        # We'll create it with no network, then attach.
        container_id = await self.docker.start_container(
            name=name,
            image=image,
            command=config.get("command", []),
            volumes=config.get("volumes", {}),
            network=None,  # not attached yet
            ip=None,
            environment=config.get("environment", {}),
        )
        # Attach to the AND bridge
        bridge_name = f"netengines_and_{network}"
        # Assign an IP from the bridge's subnet (we can let Docker assign dynamically)
        await self.docker.connect_network(container_id, bridge_name, ip=None)
        return container_id

    async def _get_gateway_ip(self, and_name: str) -> str:
        """Query Supabase for the CIDR of this AND and derive gateway IP."""
        import ipaddress

        result = (
            await self.supabase.table("address_leases")
            .select("cidr")
            .eq("and_name", and_name)
            .execute()
        )
        if not result.data:
            raise RuntimeError(f"AND {and_name} not found")
        cidr = result.data[0]["cidr"]
        # Gateway IP is the first usable IP in the CIDR block
        network = ipaddress.ip_network(cidr, strict=False)
        gateway_ip = str(network.network_address + 1)
        return gateway_ip

    async def _inject_cert(self, container_id: str, domain: str, cert: str, key: str) -> None:
        """Write cert and key into the container (via volume or exec)."""
        # For simplicity, we use a volume mount shared between app and host.
        # Or we can write via `docker exec` with `cat` redirection.
        # We'll use exec to write to /etc/ssl/certs.
        # Create temporary files and copy them.
        # We'll use a shared directory on the host mounted into the container.
        # For MVP, we'll just rely on the container image to fetch certs via volume.
        # We'll store certs in a volume named `netengines_app_certs_{container_id}`
        volume_name = f"netengines_certs_{container_id}"
        await self.docker.ensure_volume(volume_name)
        # Write cert/key to the volume using a temporary container
        # Or we can mount the volume and write using host filesystem.
        # Simpler: we'll mount the volume to the app container and put certs there.
        # But we need the container to be restarted.
        # For now, we assume the app container mounts /certs and reads from there.
        # We'll write the files now.
        import os

        cert_dir = f"/var/lib/netengines/certs/{container_id}"
        os.makedirs(cert_dir, exist_ok=True)
        with open(f"{cert_dir}/tls.crt", "w") as f:
            f.write(cert)
        with open(f"{cert_dir}/tls.key", "w") as f:
            f.write(key)
        # Now we need to mount this directory into the running container.
        # Docker doesn't allow mounting new volumes to a running container.
        # Option A: Stop container, re‑create with mount, start again.
        # Option B: Use `docker cp` to copy files into the running container.
        # We'll use `docker cp`.
        await self.docker.copy_to_container(
            container_id, f"{cert_dir}/tls.crt", "/etc/ssl/certs/tls.crt"
        )
        await self.docker.copy_to_container(
            container_id, f"{cert_dir}/tls.key", "/etc/ssl/private/tls.key"
        )

    def _get_app_image(self, app_name: str) -> str:
        """Map catalog app names to Docker images."""
        catalog = {
            "gitea": "gitea/gitea:latest",
            "wordpress": "wordpress:latest",
            "nextcloud": "nextcloud:latest",
        }
        return catalog.get(app_name, app_name)
