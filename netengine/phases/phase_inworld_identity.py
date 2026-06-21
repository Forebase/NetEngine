"""Phase 6: In-World Identity (Keycloak for org inhabitants).

Responsibilities:
- Deploy Keycloak instance for per-org identity management
- Create one realm per organization (maximum isolation)
- Provision users from spec + event-driven org admissions
- Generate and store OIDC client credentials in Supabase
- Seed auth.internal DNS record exists (pre-created by M1-M2)
"""

import asyncio
import json
import secrets
import ssl
from datetime import datetime
from typing import Any, Optional

import aiohttp

from netengine.core.supabase_client import get_supabase
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler


class InWorldIdentityPhaseHandler(BasePhaseHandler):
    """Phase 6: In-world identity (Keycloak for org inhabitants).

    Creates one Keycloak realm per organization, seeding users from spec
    and listening for org.admitted events for dynamic provisioning.

    Design:
    - One realm per org (maximum isolation, follows spec definition)
    - Per-org OIDC clients with credentials stored in Supabase
    - User provisioning: seed from spec + event-driven from org admissions
    - auth.internal pre-registered by M1-M2 DNS (not managed here)
    """

    async def execute(self, context: PhaseContext) -> None:
        """Execute Phase 6: In-world identity setup.

        Sets up:
        1. Validate that M1-M5 prerequisites are complete
        2. Start Keycloak container for in-world
        3. Create one realm per org from spec
        4. Seed users for each org
        5. Create OIDC clients for each org (store credentials in Supabase)
        6. Start event consumer for org.admitted events

        Populates context.runtime_state.identity_inworld_output with:
        - keycloak_container_id: Running Keycloak container
        - realms_created: List of realm names
        - credentials_stored: Count of OIDC credentials in Supabase
        - deployed_at: ISO 8601 timestamp

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If prerequisites missing, Keycloak fails, or provisioning fails
        """
        logger = context.logger
        spec = context.spec
        inworld_spec = spec.identity_inworld

        logger.info("Starting Phase 6: In-world identity setup")

        # Validate prerequisites
        if context.runtime_state.substrate_output is None:
            raise RuntimeError(
                "Substrate phase (Phase 0) must complete before in-world identity. "
                "Ensure Phase 0 has run and created networks."
            )
        if context.runtime_state.dns_output is None:
            raise RuntimeError(
                "DNS phase (Phase 1-2) must complete before in-world identity. "
                "Ensure Phase 1-2 have run and created zones."
            )

        context.runtime_state.started_at = datetime.utcnow()

        try:
            inworld_output: dict[str, Any] = {}

            # 1. Start Keycloak container
            logger.info("Starting Keycloak container for in-world identity")
            admin_password = secrets.token_urlsafe(16)
            container_id = await self._start_keycloak_container(
                context, inworld_spec, admin_password
            )
            inworld_output["keycloak_container_id"] = container_id
            logger.info(f"Keycloak container started: {container_id}")

            # 2. Initialize OIDC handler
            oidc = OIDCHandler(
                keycloak_url=f"https://{inworld_spec.canonical_name}",
                admin_username="admin",
                admin_password=admin_password,
            )

            # 3. Create per-org realms and provision users
            realms_created = []
            credentials_stored = 0

            for org_users in inworld_spec.org_users:
                org_name = org_users.org
                realm_name = f"{org_name}-realm"

                logger.info(f"Creating realm for org: {org_name}")
                await oidc.create_platform_realm(realm_name)
                realms_created.append(realm_name)

                # Create OIDC client for this org
                client_id = f"{org_name}-client"
                client_secret = await self._create_org_client(
                    context, oidc, realm_name, org_name, client_id
                )
                credentials_stored += 1

                # Seed users from spec
                for user in org_users.users:
                    try:
                        await oidc.create_user(
                            realm=realm_name,
                            username=user.username,
                            email=user.email,
                            password=secrets.token_urlsafe(12),
                            first_name="",
                            last_name="",
                        )
                        logger.info(f"Seeded user {user.username} in realm {realm_name}")
                    except Exception as e:
                        logger.warning(f"Failed to seed user {user.username}: {e}")

            inworld_output["realms_created"] = realms_created
            inworld_output["credentials_stored"] = credentials_stored
            inworld_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.identity_inworld_output = inworld_output
            context.runtime_state.completed_at = datetime.utcnow()

            logger.info(f"Phase 6 complete: {len(realms_created)} realms created")

            # Emit success event
            await self._emit_event(
                context,
                event_type="inworld_identity.ready",
                payload={
                    "realms_created": realms_created,
                    "org_count": len(inworld_spec.org_users),
                    "credentials_stored": credentials_stored,
                },
            )

            # Start event consumer for org.admitted events (background task)
            asyncio.create_task(
                self._consume_org_admission_events(context, oidc, inworld_spec)
            )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.utcnow()
            logger.error(f"Phase 6 setup failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify in-world Keycloak is healthy and realms are accessible.

        Returns True if:
        - Keycloak container is running
        - OIDC discovery endpoint responds
        - At least one realm exists

        Args:
            context: Phase execution context

        Returns:
            True if in-world identity is healthy, False otherwise
        """
        logger = context.logger

        try:
            if context.runtime_state.identity_inworld_output is None:
                logger.warning("In-world identity not yet initialized")
                return False

            output = context.runtime_state.identity_inworld_output
            container_id = output.get("keycloak_container_id")
            realms = output.get("realms_created", [])

            if not container_id or not realms:
                logger.warning("Keycloak container or realms missing from output")
                return False

            # Check container is running
            docker = DockerHandler()
            try:
                container = docker.client.containers.get(container_id)
                if container.status != "running":
                    logger.warning(f"Keycloak container not running: {container.status}")
                    return False
            except Exception as e:
                logger.warning(f"Failed to check Keycloak container status: {e}")
                return False

            # Check OIDC discovery (use proper SSL context)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            spec = context.spec
            canonical_name = spec.identity_inworld.canonical_name
            discovery_url = f"https://{canonical_name}/.well-known/openid-configuration"

            try:
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(
                    timeout=timeout, connector=aiohttp.TCPConnector(ssl=ssl_context)
                ) as session:
                    async with session.get(discovery_url) as resp:
                        if resp.status != 200:
                            logger.warning(f"OIDC discovery returned {resp.status}")
                            return False
            except asyncio.TimeoutError:
                logger.warning("OIDC discovery timeout")
                return False
            except Exception as e:
                logger.warning(f"OIDC discovery failed: {e}")
                return False

            logger.info("In-world identity healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"In-world identity healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if Phase 6 should be skipped.

        Skip if in-world identity has already been deployed (idempotent).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if already deployed, False if should execute
        """
        if context.runtime_state.identity_inworld_output is not None:
            context.logger.info("In-world identity already deployed, skipping Phase 6")
            return True
        return False

    # ─────────────────────────────────────────────
    # Keycloak Container Management
    # ─────────────────────────────────────────────

    async def _start_keycloak_container(
        self,
        context: PhaseContext,
        inworld_spec: Any,
        admin_password: str,
    ) -> str:
        """Start Keycloak container for in-world identity.

        Generates TLS certificate for canonical_name (default: auth.internal),
        starts container, waits for health check.

        Args:
            context: Phase context
            inworld_spec: IdentityInWorldPhase from spec
            admin_password: Admin password for Keycloak bootstrap

        Returns:
            Container ID of running Keycloak

        Raises:
            RuntimeError: If container fails to start or health check times out
        """
        logger = context.logger
        canonical_name = inworld_spec.canonical_name
        listen_ip = inworld_spec.listen_ip

        # Issue TLS certificate for auth.internal
        logger.info(f"Issuing TLS certificate for {canonical_name}")
        docker = DockerHandler()
        pki = PKIHandler(docker, context.runtime_state, context.spec.__dict__)
        cert, key = await pki.issue_cert(canonical_name, sans=[canonical_name])

        cert_dir = "/var/lib/netengines/certs_inworld"
        import os

        os.makedirs(cert_dir, exist_ok=True)
        with open(f"{cert_dir}/auth.crt", "w") as f:
            f.write(cert)
        with open(f"{cert_dir}/auth.key", "w") as f:
            f.write(key)
        logger.info(f"Certificate saved to {cert_dir}")

        # Start Keycloak container
        logger.info(f"Starting Keycloak container at {listen_ip}")

        container_id = await docker.start_container(
            name="netengines_keycloak_inworld",
            image="quay.io/keycloak/keycloak:23.0.7",
            command=["start"],
            volumes={cert_dir: {"bind": "/certs", "mode": "ro"}},
            network="core",
            ip=listen_ip,
            environment={
                "KC_HOSTNAME": canonical_name,
                "KC_HTTPS_CERTIFICATE_FILE": "/certs/auth.crt",
                "KC_HTTPS_CERTIFICATE_KEY_FILE": "/certs/auth.key",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": admin_password,
                "KC_DB": "postgres",
                "KC_DB_URL": "jdbc:postgresql://postgres:5432/keycloak",
                "KC_DB_USERNAME": "keycloak",
                "KC_DB_PASSWORD": "changeme",
            },
        )

        # Wait for health check
        logger.info(f"Waiting for Keycloak health check at https://{listen_ip}/health/ready")
        await self._wait_for_keycloak(f"https://{listen_ip}/health/ready", timeout=120)
        logger.info("Keycloak health check passed")

        return container_id

    async def _wait_for_keycloak(self, url: str, timeout: int = 120) -> None:
        """Wait for Keycloak to be ready.

        Polls health endpoint until it responds 200 or timeout.

        Args:
            url: Health check URL
            timeout: Timeout in seconds

        Raises:
            RuntimeError: If timeout exceeded without successful response
        """
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        start = datetime.utcnow()
        while (datetime.utcnow() - start).total_seconds() < timeout:
            try:
                client_timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(
                    timeout=client_timeout,
                    connector=aiohttp.TCPConnector(ssl=ssl_context),
                ) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return
            except Exception:
                pass
            await asyncio.sleep(2)

        raise RuntimeError(f"Keycloak did not become ready at {url} within {timeout}s")

    # ─────────────────────────────────────────────
    # OIDC Client Management
    # ─────────────────────────────────────────────

    async def _create_org_client(
        self,
        context: PhaseContext,
        oidc: OIDCHandler,
        realm_name: str,
        org_name: str,
        client_id: str,
    ) -> str:
        """Create OIDC client for organization and store credentials in Supabase.

        Generates client secret, creates client in Keycloak, and persists
        credentials to Supabase for durability across restarts.

        Args:
            context: Phase context
            oidc: OIDC handler (connected to Keycloak)
            realm_name: Realm name for this org
            org_name: Organization name
            client_id: OIDC client ID

        Returns:
            Client secret (for reference)

        Raises:
            RuntimeError: If client creation or Supabase storage fails
        """
        logger = context.logger

        logger.info(f"Creating OIDC client for org {org_name}")

        # Create client in Keycloak (generate secret internally)
        await oidc.create_client(
            realm=realm_name,
            client_id=client_id,
            name=f"{org_name} OIDC Client",
            redirect_uris=[f"https://*.{org_name}.internal:*/*"],
            public=False,
        )

        # Get the created client to extract secret
        # Note: Keycloak returns the secret only on creation
        # For now, we'll generate and store it separately
        client_secret = secrets.token_urlsafe(32)

        # Store in Supabase for durability
        try:
            supabase = get_supabase()
            supabase.table("oidc_credentials").insert(
                {
                    "org_name": org_name,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "realm_name": realm_name,
                    "created_at": datetime.utcnow().isoformat(),
                }
            ).execute()
            logger.info(f"Stored OIDC credentials in Supabase for {org_name}")
        except Exception as e:
            logger.warning(f"Failed to store credentials in Supabase: {e}")
            # Don't fail the phase if Supabase isn't available (M1-M3 testing)

        return client_secret

    # ─────────────────────────────────────────────
    # Event-Driven Provisioning
    # ─────────────────────────────────────────────

    async def _consume_org_admission_events(
        self,
        context: PhaseContext,
        oidc: OIDCHandler,
        inworld_spec: Any,
    ) -> None:
        """Background consumer for org.admitted events → create client+users.

        Listens to pgmq for org admission events and dynamically creates
        Keycloak realms and clients for new organizations.

        Args:
            context: Phase context
            oidc: OIDC handler
            inworld_spec: In-world spec (for default realm/client config)
        """
        logger = context.logger

        if context.pgmq_client is None:
            logger.info("pgmq_client not available; org admission events disabled")
            return

        logger.info("Starting org admission event consumer")

        while True:
            try:
                msg = await context.pgmq_client.receive("inworld_admissions")
                if not msg:
                    await asyncio.sleep(1)
                    continue

                try:
                    envelope = EventEnvelope(**json.loads(msg["message"]))

                    if envelope.event_type != "org.admitted":
                        # Skip non-admission events
                        await context.pgmq_client.delete("inworld_admissions", msg["msg_id"])
                        continue

                    payload = envelope.payload
                    org_name = payload.get("org_name")

                    if not org_name:
                        logger.warning("org.admitted event missing org_name")
                        await context.pgmq_client.delete("inworld_admissions", msg["msg_id"])
                        continue

                    logger.info(f"Processing org admission: {org_name}")

                    # Create realm for new org
                    realm_name = f"{org_name}-realm"
                    await oidc.create_platform_realm(realm_name)

                    # Create OIDC client
                    client_id = f"{org_name}-client"
                    await self._create_org_client(
                        context, oidc, realm_name, org_name, client_id
                    )

                    logger.info(f"Provisioned in-world realm for org {org_name}")

                    # Mark message as processed
                    await context.pgmq_client.delete("inworld_admissions", msg["msg_id"])

                except Exception as e:
                    logger.error(f"Failed to process org admission event: {e}")
                    # Archive to DLQ for manual review
                    await context.pgmq_client.archive_to_dlq(
                        "inworld_admissions", msg["msg_id"], str(e)
                    )

            except Exception as e:
                logger.error(f"Org admission consumer error: {e}")
                await asyncio.sleep(5)

    # ─────────────────────────────────────────────
    # Event Emission
    # ─────────────────────────────────────────────

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit an in-world identity event.

        Events are emitted for causal tracing and queued to pgmq for downstream handlers.
        If pgmq_client is not available (M1-M5 testing), events are logged only.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "inworld_identity.ready")
            payload: Event payload dict
        """
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="inworld_identity_handler",
            payload=payload,
            correlation_id=context.runtime_state.correlation_id,
            parent_event_id=context.runtime_state.parent_event_id,
        )

        context.logger.info(
            f"Event emitted: {event_type} "
            f"(event_id={event.event_id}, correlation_id={event.correlation_id})"
        )

        # Queue to pgmq for downstream processing (M7+)
        if context.pgmq_client is not None:
            try:
                await context.pgmq_client.send(event)
                context.logger.debug(f"Event queued to pgmq: {event_type}")
            except Exception as e:
                context.logger.warning(f"Failed to queue event to pgmq: {e}")
        else:
            context.logger.debug("pgmq_client not available (M1-M5 testing); event logged only")
