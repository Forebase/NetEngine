import asyncio
import json
import os
import secrets
from datetime import datetime

from netengine.core.pgmq_client import PGMQClient
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler


class InWorldIdentityPhaseHandler(BasePhaseHandler):
    """Phase 6: In‑world identity (Keycloak for org inhabitants)."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec
        inworld_spec = spec.get("identity_inworld", {})

        # 1. Start Keycloak container for in‑world
        logger.info("Starting in‑world Keycloak container")
        admin_password = secrets.token_urlsafe(16)
        container_id = await self._start_inworld_keycloak(context, inworld_spec, admin_password)
        context.runtime_state.inworld_keycloak_container_id = container_id
        context.runtime_state.inworld_admin_password = admin_password
        context.runtime_state.save()

        # 2. Register DNS record for auth.internal
        dns = DNSHandler()
        listen_ip = inworld_spec.get("listen_ip", "10.0.0.12")
        await dns.add_zone_record("internal", "A", "auth", listen_ip, 300)

        # 3. Bootstrap the in‑world realm
        oidc = OIDCHandler(
            keycloak_url=f"https://auth.internal",
            admin_username="admin",
            admin_password=admin_password,
        )
        realm_name = inworld_spec.get("realm_name", "inworld")
        await oidc.create_platform_realm(realm_name)  # reuse method to create realm

        # 4. Seed org users from spec
        org_users = inworld_spec.get("org_users", [])
        for entry in org_users:
            org = entry["org"]
            for user in entry.get("users", []):
                await oidc.create_user(
                    realm=realm_name,
                    username=user["username"],
                    email=user["email"],
                    password=user.get("password", secrets.token_urlsafe(12)),
                    first_name=user.get("first_name", ""),
                    last_name=user.get("last_name", ""),
                )
            # Also create a client for this org
            client_id = f"{org}-client"
            await oidc.create_client(
                realm=realm_name,
                client_id=client_id,
                name=f"{org} OIDC Client",
                redirect_uris=["https://*"],
                public=False,
            )

        # 5. Start pgmq consumer for future org admissions
        asyncio.create_task(self._consume_org_admissions(context, oidc, realm_name))

        # 6. Update state
        context.runtime_state.phase_completed["6"] = True
        context.runtime_state.save()
        logger.info("Phase 6 complete: in‑world identity ready")

    async def _start_inworld_keycloak(self, context, inworld_spec, admin_password) -> str:
        """Start Keycloak container for in‑world."""
        # Issue cert for auth.internal
        pki = PKIHandler(None, context.runtime_state, context.spec)  # need docker instance
        cert, key = await pki.issue_cert("auth.internal", [])

        cert_dir = "/var/lib/netengines/certs_inworld"
        os.makedirs(cert_dir, exist_ok=True)
        with open(f"{cert_dir}/auth.crt", "w") as f:
            f.write(cert)
        with open(f"{cert_dir}/auth.key", "w") as f:
            f.write(key)

        docker = DockerHandler()
        listen_ip = inworld_spec.get("listen_ip", "10.0.0.12")
        container_id = await docker.start_container(
            name="netengines_keycloak_inworld",
            image="quay.io/keycloak/keycloak:23.0.7",
            command=["start"],
            volumes={cert_dir: {"bind": "/certs", "mode": "ro"}},
            network="core",
            ip=listen_ip,
            environment={
                "KC_HOSTNAME": "auth.internal",
                "KC_HTTPS_CERTIFICATE_FILE": "/certs/auth.crt",
                "KC_HTTPS_CERTIFICATE_KEY_FILE": "/certs/auth.key",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": admin_password,
            },
        )
        # Wait for health
        await self._wait_for_keycloak(f"https://{listen_ip}/health/ready")
        return container_id

    async def _wait_for_keycloak(self, url: str, timeout: int = 60):
        import asyncio

        import aiohttp

        start = datetime.utcnow()
        while (datetime.utcnow() - start).total_seconds() < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, ssl=False) as resp:
                        if resp.status == 200:
                            return
            except Exception:
                pass
            await asyncio.sleep(2)
        raise RuntimeError("Keycloak did not become ready")

    async def _consume_org_admissions(self, context, oidc: OIDCHandler, realm_name: str):
        """Background consumer for org.admitted events -> create client+users."""
        pgmq = PGMQClient()
        while True:
            msg = await pgmq.receive("oidc_provisioning")
            if not msg:
                await asyncio.sleep(1)
                continue
            try:
                envelope = EventEnvelope(**json.loads(msg["message"]))
                if envelope.event_type != "org.admitted":
                    await pgmq.delete("oidc_provisioning", msg["msg_id"])
                    continue
                payload = envelope.payload
                org = payload["org_name"]
                # Create client for org
                client_id = f"{org}-client"
                await oidc.create_client(
                    realm=realm_name,
                    client_id=client_id,
                    name=f"{org} OIDC Client",
                    redirect_uris=["https://*"],
                    public=False,
                )
                # Users are not automatically created on admission – could be extended.
                # For now, we just log.
                context.logger.info(f"Created client for org {org} in in‑world realm")
                await pgmq.delete("oidc_provisioning", msg["msg_id"])
            except Exception as e:
                await pgmq.archive_to_dlq("oidc_provisioning", msg["msg_id"], str(e))
