from datetime import datetime
import os
import secrets
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.dns import DNSHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.core.supabase_client import get_supabase
from netengine.utils.run_migrations import apply_migrations

class PlatformIdentityPhaseHandler(BasePhaseHandler):
    """Phase 4: Platform identity (Keycloak + Supabase)."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec

        # 1. Run Supabase migrations (idempotent)
        logger.info("Running Supabase migrations...")
        await apply_migrations()

        # 2. Generate or retrieve bootstrap admin password for Keycloak
        admin_password = context.runtime_state.get("bootstrap_admin_password")
        if not admin_password:
            admin_password = secrets.token_urlsafe(16)
            context.runtime_state.bootstrap_admin_password = admin_password
            context.runtime_state.save()
            logger.info(f"Keycloak admin password generated: {admin_password}")

        # 3. Start Keycloak container
        # Get TLS cert from PKI (already available via PKIHandler)
        pki = PKIHandler(DockerHandler(), context.runtime_state, spec)  # import needed
        cert, key = await pki.issue_cert("auth.platform.internal", [])
        # Write cert/key to a temporary volume or directory.
        # For simplicity, we'll mount a host directory with the certs.
        cert_dir = "/var/lib/netengines/certs"
        os.makedirs(cert_dir, exist_ok=True)
        with open(f"{cert_dir}/auth.crt", "w") as f:
            f.write(cert)
        with open(f"{cert_dir}/auth.key", "w") as f:
            f.write(key)

        docker = DockerHandler()
        container_id = await docker.start_container(
            name="netengines_keycloak_platform",
            image="quay.io/keycloak/keycloak:23.0.7",
            command=["start"],
            volumes={cert_dir: {"bind": "/certs", "mode": "ro"}},
            network="core",
            ip="10.0.0.7",  # from spec identity_platform.listen_ip
            environment={
                "KC_HOSTNAME": "auth.platform.internal",
                "KC_HTTPS_CERTIFICATE_FILE": "/certs/auth.crt",
                "KC_HTTPS_CERTIFICATE_KEY_FILE": "/certs/auth.key",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": admin_password,
            }
        )
        context.runtime_state.keycloak_platform_container_id = container_id
        context.runtime_state.save()

        # Wait for Keycloak to be ready (healthcheck)
        await self._wait_for_keycloak("https://10.0.0.7/health/ready")

        # 4. Register DNS record for auth.platform.internal
        dns = DNSHandler()  # or get from context
        await dns.add_zone_record("platform.internal", "A", "auth", "10.0.0.7", 300)

        # 5. Bootstrap platform realm via OIDC handler
        oidc = OIDCHandler(
            keycloak_url="https://auth.platform.internal",
            admin_username="admin",
            admin_password=admin_password
        )
        realm_id = await oidc.create_platform_realm("platform")
        user_id = await oidc.create_admin_user(
            realm="platform",
            username="admin",
            email="admin@platform.internal",
            password=admin_password
        )
        context.runtime_state.platform_realm_id = realm_id
        context.runtime_state.admin_user_id = user_id
        context.runtime_state.phase_completed["4"] = True
        context.runtime_state.save()

        logger.info("Phase 4 complete: platform identity bootstrapped")

    async def _wait_for_keycloak(self, url: str, timeout: int = 60):
        import aiohttp
        import asyncio
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
        raise RuntimeError("Keycloak did not become ready in time")