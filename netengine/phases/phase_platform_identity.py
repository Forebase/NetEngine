import os
import secrets
from datetime import UTC, datetime

from netengine.db.migrations import run_migrations
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler
from logs import get_logger

logger = get_logger(__name__)


class PlatformIdentityPhaseHandler(BasePhaseHandler):
    """Phase 4: Platform identity (Keycloak + Supabase)."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec

        # 1. Run Supabase migrations (idempotent)
        logger.info("Running Supabase migrations...")
        await run_migrations()

        # 2. Generate or retrieve bootstrap admin password for Keycloak
        admin_password = getattr(context.runtime_state, "bootstrap_admin_password", None)
        if not admin_password:
            admin_password = secrets.token_urlsafe(16)
            context.runtime_state.bootstrap_admin_password = admin_password
            context.runtime_state.save()
            logger.info(f"Keycloak admin password generated: {admin_password}")

        # 3. Start Keycloak container
        # Get TLS cert from PKI (already available via PKIHandler)
        pki = PKIHandler(DockerHandler(), context.runtime_state, spec)  # import needed
        cert, key = await pki.issue_cert("auth.platform.internal", [])

        # Track issued certificate in RuntimeState
        expiry = pki.extract_cert_expiry(cert)
        context.runtime_state.issued_certificates["auth.platform.internal"] = {
            "cert_type": "platform_identity",
            "issued_at": datetime.now(UTC).isoformat(),
            "expires_at": expiry.isoformat(),
            "sans": [],
            "rotated_at": None,
            "version": 1,
        }

        # Write cert/key to a temporary volume or directory.
        # For simplicity, we'll mount a host directory with the certs.
        cert_dir = "/var/lib/netengines/certs"
        os.makedirs(cert_dir, exist_ok=True)
        with open(f"{cert_dir}/auth.crt", "w") as f:
            f.write(cert)
        with open(f"{cert_dir}/auth.key", "w") as f:
            f.write(key)

        auth_ip = spec.identity_platform.listen_ip
        auth_hostname = spec.identity_platform.canonical_name

        docker = DockerHandler()
        container_id = await docker.start_container(
            name="netengines_keycloak_platform",
            image="quay.io/keycloak/keycloak:23.0.7",
            command=["start"],
            volumes={cert_dir: {"bind": "/certs", "mode": "ro"}},
            network="core",
            ip=auth_ip,
            environment={
                "KC_HOSTNAME": auth_hostname,
                "KC_HTTPS_CERTIFICATE_FILE": "/certs/auth.crt",
                "KC_HTTPS_CERTIFICATE_KEY_FILE": "/certs/auth.key",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": admin_password,
            },
        )
        context.runtime_state.keycloak_platform_container_id = container_id
        context.runtime_state.save()

        # Wait for Keycloak to be ready (healthcheck)
        await self._wait_for_keycloak(f"https://{auth_ip}/health/ready")

        # 4. Register DNS record for auth.platform.internal
        dns = DNSHandler()  # or get from context
        await dns.add_zone_record(context, "platform.internal", "A", "auth", auth_ip, 300)

        # 5. Bootstrap platform realm via OIDC handler
        oidc = OIDCHandler(
            keycloak_url="https://auth.platform.internal",
            admin_username="admin",
            admin_password=admin_password,
        )
        realm_id = await oidc.create_platform_realm("platform")
        user_id = await oidc.create_admin_user(
            realm="platform",
            username="admin",
            email="admin@platform.internal",
            password=admin_password,
        )

        # Create platform client for API authentication
        client_id, client_secret = await oidc.create_client(
            realm="platform",
            client_id="platform-api",
            name="Platform API",
            redirect_uris=["https://api.platform.internal/callback"],
            public=False,
            return_secret=True,
        )

        # Add token mapper to include org claim in JWT
        await oidc.add_token_mapper(
            realm="platform",
            client_id=client_id,
            mapper_name="org-claim-mapper",
            protocol_mapper_type="oidc-usermodel-property-mapper",
            config={
                "user.attribute": "org",
                "claim.name": "org",
                "jsonType.label": "String",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
            },
        )

        context.runtime_state.platform_realm_id = realm_id
        context.runtime_state.admin_user_id = user_id
        context.runtime_state.platform_client_id = client_id
        context.runtime_state.platform_client_auth_id = "platform-api"
        context.runtime_state.platform_client_secret = client_secret
        context.runtime_state.identity_platform_output = {
            "keycloak_container_id": container_id,
            "platform_realm_id": realm_id,
            "admin_user_id": user_id,
            "platform_client_id": client_id,
            "platform_client_auth_id": "platform-api",
            "platform_client_secret": client_secret,
            "deployed_at": datetime.utcnow().isoformat(),
            "deployed_at": datetime.now(UTC).isoformat(),
        }
        context.runtime_state.phase_completed["4"] = True
        context.runtime_state.save()

        logger.info("Phase 4 complete: platform identity bootstrapped")

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Check if Keycloak platform is ready."""
        import asyncio

        import aiohttp

        try:
            # Check if container ID is set
            container_id = getattr(context.runtime_state, "keycloak_platform_container_id", None)
            if not container_id:
                return False

            # Check container is running
            docker = DockerHandler()
            try:
                container = docker.client.containers.get(container_id)
                if container.status != "running":
                    return False
            except Exception as exc:
                logger.debug(f"Could not inspect Keycloak platform container {container_id}: {exc}")
                return False

            # Check OIDC discovery endpoint
            ssl_context = __import__("ssl").create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = __import__("ssl").CERT_NONE

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(
                        "https://auth.platform.internal/.well-known/openid-configuration",
                        ssl=ssl_context,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        return resp.status == 200
                except asyncio.TimeoutError:
                    return False
                except aiohttp.ClientError:
                    return False
        except Exception as exc:
            logger.warning(f"Platform identity healthcheck error: {exc}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Skip if Phase 4 already completed."""
        return context.runtime_state.phase_completed.get("4", False)

    async def _wait_for_keycloak(self, url: str, timeout: int = 60):
        import asyncio

        import aiohttp

        start = datetime.now(UTC)
        while (datetime.now(UTC) - start).total_seconds() < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, ssl=False) as resp:
                        if resp.status == 200:
                            return
            except Exception as exc:
                logger.debug(f"Keycloak not ready yet ({url}): {exc}")
            await asyncio.sleep(2)
        raise RuntimeError("Keycloak did not become ready in time")
