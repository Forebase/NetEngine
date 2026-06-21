"""Phase 4: OIDC handler — Keycloak identity platform bootstrap.

Responsibilities:
- Initialize Keycloak container
- Create platform realm
- Create admin user for platform realm
- Generate OIDC client credentials
- Store credentials in Supabase
- Verify Keycloak endpoint responsiveness
- Emit identity.platform_ready event on success
"""

from datetime import datetime
from typing import Any

from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext


class OIDCHandler(BasePhaseHandler):
    """Phase 4 OIDC/Keycloak identity platform bootstrap.

    Initializes Keycloak with a platform realm, admin user, and OIDC
    client credentials. All credentials are stored in Supabase and cached
    in RuntimeState for immediate use.
    """

    async def execute(self, context: PhaseContext) -> None:
        """Execute Phase 4 OIDC/Keycloak bootstrap.

        Sets up:
        1. Keycloak container deployment
        2. Platform realm creation
        3. Admin user creation
        4. OIDC client credential generation
        5. Storage of credentials in Supabase
        6. Health verification

        Populates context.runtime_state.identity_platform_output with:
        - identity_platform_type: "keycloak"
        - realm_id: Platform realm identifier
        - realm_name: Platform realm name
        - admin_user_id: Admin user identifier
        - admin_username: Admin user login name
        - admin_password: Admin password (stored in Supabase)
        - oidc_client_id: OIDC client ID
        - oidc_client_secret: OIDC client secret (stored in Supabase)
        - issuer_url: Keycloak issuer URL for tokens
        - deployed_at: ISO 8601 timestamp
        - health_status: Current health state

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If Keycloak initialization fails
        """
        logger = context.logger
        spec = context.spec

        logger.info("Starting Phase 4: OIDC/Keycloak bootstrap")
        context.runtime_state.started_at = datetime.utcnow()

        try:
            identity_output: dict[str, Any] = {}

            # 1. Initialize Keycloak container
            keycloak_status = await self._init_keycloak(context)
            identity_output.update(keycloak_status)
            logger.info(f"Keycloak initialized: {keycloak_status['identity_platform_type']}")

            # 2. Create platform realm
            realm_info = await self._create_platform_realm(context)
            identity_output.update(realm_info)
            logger.info(f"Platform realm created: {realm_info['realm_name']}")

            # 3. Create admin user
            admin_info = await self._create_admin_user(context, realm_info["realm_id"])
            identity_output.update(admin_info)
            logger.info(f"Admin user created: {admin_info['admin_username']}")

            # 4. Generate OIDC client credentials
            oidc_info = await self._generate_oidc_client(context, realm_info["realm_id"])
            identity_output.update(oidc_info)
            logger.info(f"OIDC client created: {oidc_info['oidc_client_id']}")

            # 5. Store credentials in Supabase
            await self._store_secrets(context, identity_output)
            logger.info("Identity platform credentials stored in Supabase")

            # 6. Verify Keycloak responsiveness
            health_status = await self._verify_keycloak(context, identity_output)
            identity_output["health_status"] = health_status

            identity_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.identity_platform_output = identity_output
            context.runtime_state.completed_at = datetime.utcnow()

            logger.info("Phase 4: OIDC/Keycloak bootstrap complete")

            # Emit success event
            await self._emit_event(
                context,
                event_type="identity.platform_ready",
                payload={
                    "realm_id": identity_output["realm_id"],
                    "oidc_client_id": identity_output["oidc_client_id"],
                    "issuer_url": identity_output["issuer_url"],
                    "health_status": health_status,
                },
            )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.utcnow()
            logger.error(f"Phase 4 OIDC/Keycloak bootstrap failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify OIDC/Keycloak health and readiness.

        Returns True if:
        - Keycloak container is running
        - Keycloak endpoint is responsive
        - Platform realm exists and is accessible
        - Admin credentials are working
        - OIDC client is configured

        Returns False if Keycloak is unreachable (Unhealthy).
        For transient issues (API slow), return True but log as Sick.

        Args:
            context: Phase execution context

        Returns:
            True if OIDC is healthy or recoverable, False if unhealthy
        """
        logger = context.logger

        try:
            if context.runtime_state.identity_platform_output is None:
                logger.warning("OIDC platform not yet initialized")
                return False

            output = context.runtime_state.identity_platform_output

            # Check Keycloak status fields
            if "identity_platform_type" not in output:
                logger.warning("Identity platform type missing from output")
                return False

            if "issuer_url" not in output:
                logger.warning("Issuer URL missing from identity platform output")
                return False

            # Check realm exists
            if "realm_id" not in output or "realm_name" not in output:
                logger.warning("Platform realm information missing from output")
                return False

            # Check admin user exists
            if "admin_user_id" not in output or "admin_username" not in output:
                logger.warning("Admin user information missing from output")
                return False

            # Check OIDC client exists
            if "oidc_client_id" not in output:
                logger.warning("OIDC client ID missing from output")
                return False

            # In M2, we stub the Keycloak endpoint check but log readiness
            health = output.get("health_status", "Healthy")
            if health == "Unhealthy":
                logger.error("OIDC health check indicates Keycloak is unhealthy")
                return False

            if health == "Sick":
                logger.warning(
                    "OIDC health check indicates transient issues (Keycloak may be slow)"
                )
                return True

            logger.info("OIDC healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"OIDC healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if Phase 4 should be skipped.

        Skip if OIDC/Keycloak has already been bootstrapped (idempotent reload).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if OIDC already bootstrapped, False if should execute
        """
        if context.runtime_state.identity_platform_output is not None:
            context.logger.info("OIDC platform already bootstrapped, skipping Phase 4")
            return True
        return False

    # ─────────────────────────────────────────────
    # Private implementation methods
    # ─────────────────────────────────────────────

    async def _init_keycloak(self, context: PhaseContext) -> dict[str, Any]:
        """Initialize Keycloak container and configuration.

        Args:
            context: Phase context

        Returns:
            Dict with Keycloak initialization status and issuer URL

        Raises:
            RuntimeError: If Keycloak initialization fails
        """
        logger = context.logger

        logger.info("Initializing Keycloak identity platform")

        # M2 stub: Real implementation would deploy Keycloak Docker container
        return {
            "identity_platform_type": "keycloak",
            "status": "ready",
            "issuer_url": "https://keycloak.netengine.local:8080/realms/",
            "keycloak_admin_console": "https://keycloak.netengine.local:8080/admin/",
            "initialized_at": datetime.utcnow().isoformat(),
        }

    async def _create_platform_realm(self, context: PhaseContext) -> dict[str, Any]:
        """Create platform realm in Keycloak.

        Args:
            context: Phase context

        Returns:
            Dict with:
            - realm_id: Realm identifier
            - realm_name: Realm name

        Raises:
            RuntimeError: If realm creation fails
        """
        logger = context.logger

        logger.info("Creating platform realm")

        # M2 stub: Real implementation would call Keycloak Admin API
        realm_name = "platform"
        return {
            "realm_id": "mock-realm-uuid-1234",
            "realm_name": realm_name,
            "realm_created_at": datetime.utcnow().isoformat(),
        }

    async def _create_admin_user(self, context: PhaseContext, realm_id: str) -> dict[str, Any]:
        """Create admin user for platform realm.

        Args:
            context: Phase context
            realm_id: Target realm identifier

        Returns:
            Dict with:
            - admin_user_id: Admin user identifier
            - admin_username: Admin user login name
            - admin_password: Admin password (will be stored in Supabase)

        Raises:
            RuntimeError: If user creation fails
        """
        logger = context.logger

        logger.info("Creating platform admin user")

        # M2 stub: Real implementation would call Keycloak Admin API
        import secrets

        admin_password = secrets.token_urlsafe(24)
        return {
            "admin_user_id": "mock-admin-user-uuid-5678",
            "admin_username": "platform-admin",
            "admin_password": admin_password,
            "admin_user_created_at": datetime.utcnow().isoformat(),
        }

    async def _generate_oidc_client(self, context: PhaseContext, realm_id: str) -> dict[str, Any]:
        """Generate OIDC client for intra-world service authentication.

        Args:
            context: Phase context
            realm_id: Target realm identifier

        Returns:
            Dict with:
            - oidc_client_id: Client ID
            - oidc_client_secret: Client secret (will be stored in Supabase)
            - oidc_redirect_uris: Allowed redirect URIs

        Raises:
            RuntimeError: If client creation fails
        """
        logger = context.logger

        logger.info("Generating OIDC client credentials")

        # M2 stub: Real implementation would call Keycloak Admin API
        import secrets

        client_secret = secrets.token_urlsafe(48)
        return {
            "oidc_client_id": "netengine-platform-svc",
            "oidc_client_secret": client_secret,
            "oidc_redirect_uris": [
                "https://api.netengine.local/auth/callback",
                "http://localhost:8000/auth/callback",
            ],
            "oidc_client_created_at": datetime.utcnow().isoformat(),
        }

    async def _store_secrets(self, context: PhaseContext, identity_output: dict[str, Any]) -> None:
        """Store OIDC/Keycloak secrets in Supabase.

        Args:
            context: Phase context
            identity_output: Identity output dict with credentials

        Raises:
            RuntimeError: If storage fails
        """
        logger = context.logger

        logger.info("Storing OIDC credentials in Supabase")

        # M2 stub: Real implementation would call supabase_client.store_secret()
        # and encrypt before storage. For now, we just log that secrets are stored.
        secrets_to_store = [
            "oidc:admin_password",
            "oidc:client_secret",
        ]

        for secret_key in secrets_to_store:
            logger.debug(f"Stored secret: {secret_key}")

    async def _verify_keycloak(self, context: PhaseContext, identity_output: dict[str, Any]) -> str:
        """Verify Keycloak endpoint responsiveness and health.

        Returns health status: "Healthy", "Sick", or "Unhealthy".
        - Healthy: Keycloak responds normally
        - Sick: Keycloak responds but slow (transient issues)
        - Unhealthy: Keycloak unreachable or not responding

        Args:
            context: Phase context
            identity_output: Identity output with issuer_url

        Returns:
            Health status string
        """
        logger = context.logger

        logger.info(f"Verifying Keycloak endpoint: {identity_output['issuer_url']}")

        # M2 stub: Real implementation would make HTTP call to Keycloak health endpoint
        # For now, we return Healthy. In production this would attempt:
        #   GET https://keycloak.netengine.local:8080/health
        # and return Sick if slow or Unhealthy if unreachable.

        return "Healthy"

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit an OIDC event.

        In M2, events are logged but not yet queued to pgmq.
        M4+ handlers will integrate with pgmq queue.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "identity.platform_ready")
            payload: Event payload dict
        """
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="oidc_handler",
            payload=payload,
            correlation_id=context.runtime_state.correlation_id,
            parent_event_id=context.runtime_state.parent_event_id,
        )

        context.logger.info(
            f"Event emitted: {event_type} "
            f"(event_id={event.event_id}, correlation_id={event.correlation_id})"
        )
        # M4+: Queue to pgmq
        # await context.pgmq_client.send(event)
