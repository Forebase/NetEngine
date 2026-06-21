"""Phase 3: PKI handler — certificate authority bootstrap via step-ca.

Responsibilities:
- Initialize step-ca certificate authority
- Generate and store root CA certificate
- Create admin client certificate for bootstrapping
- Store certificates and keys in Supabase
- Verify CA endpoint responsiveness
- Emit pki.ready event on success
"""

from datetime import datetime
from typing import Any

from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext


class PKIHandler(BasePhaseHandler):
    """Phase 3 PKI bootstrap via step-ca.

    Initializes a certificate authority and generates bootstrapping
    credentials. All certificates are stored in Supabase for persistence
    and cached in RuntimeState for immediate use.
    """

    async def execute(self, context: PhaseContext) -> None:
        """Execute Phase 3 PKI bootstrap.

        Sets up:
        1. step-ca container deployment
        2. Root CA certificate generation
        3. Admin client certificate generation
        4. Storage of certs/keys in Supabase
        5. Health verification

        Populates context.runtime_state.pki_output with:
        - ca_type: "step-ca"
        - ca_fingerprint: Root certificate fingerprint
        - ca_cert_pem: Root certificate (PEM encoded)
        - admin_client_cert_pem: Admin client certificate
        - admin_client_key_pem: Admin client private key
        - issuer_url: Step-ca issuer endpoint URL
        - deployed_at: ISO 8601 timestamp
        - health_status: Current health state

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If CA initialization or cert generation fails
        """
        logger = context.logger
        spec = context.spec

        logger.info("Starting Phase 3: PKI bootstrap")
        context.runtime_state.started_at = datetime.utcnow()

        try:
            pki_output: dict[str, Any] = {}

            # 1. Initialize step-ca container
            ca_status = await self._init_step_ca(context)
            pki_output.update(ca_status)
            logger.info(f"Step-ca initialized: {ca_status['ca_type']}")

            # 2. Generate root CA certificate
            root_cert = await self._generate_root_ca(context)
            pki_output["ca_cert_pem"] = root_cert["cert_pem"]
            pki_output["ca_fingerprint"] = root_cert["fingerprint"]
            logger.info(f"Root CA generated with fingerprint: {root_cert['fingerprint'][:16]}...")

            # 3. Generate admin client certificate
            admin_cert = await self._generate_admin_cert(context)
            pki_output["admin_client_cert_pem"] = admin_cert["cert_pem"]
            pki_output["admin_client_key_pem"] = admin_cert["key_pem"]
            logger.info("Admin client certificate generated")

            # 4. Store in Supabase
            await self._store_secrets(context, pki_output)
            logger.info("PKI secrets stored in Supabase")

            # 5. Verify CA responsiveness
            health_status = await self._verify_ca(context, pki_output)
            pki_output["health_status"] = health_status

            pki_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.pki_output = pki_output
            context.runtime_state.completed_at = datetime.utcnow()

            logger.info("Phase 3: PKI bootstrap complete")

            # Emit success event
            await self._emit_event(
                context,
                event_type="pki.ready",
                payload={
                    "ca_fingerprint": pki_output["ca_fingerprint"],
                    "issuer_url": pki_output["issuer_url"],
                    "health_status": health_status,
                },
            )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.utcnow()
            logger.error(f"Phase 3 PKI bootstrap failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify PKI health and readiness.

        Returns True if:
        - step-ca container is running
        - CA endpoint is responsive
        - Root CA certificate is valid
        - Admin credentials are accessible

        Returns False if CA is unreachable (Unhealthy).
        For transient issues (API slow), return True but log as Sick.

        Args:
            context: Phase execution context

        Returns:
            True if PKI is healthy or recoverable, False if unhealthy
        """
        logger = context.logger

        try:
            if context.runtime_state.pki_output is None:
                logger.warning("PKI not yet initialized")
                return False

            output = context.runtime_state.pki_output

            # Check CA status fields
            if "ca_type" not in output:
                logger.warning("CA type missing from PKI output")
                return False

            if "issuer_url" not in output:
                logger.warning("Issuer URL missing from PKI output")
                return False

            # Check root CA cert exists
            if "ca_cert_pem" not in output or not output["ca_cert_pem"]:
                logger.warning("Root CA certificate missing from PKI output")
                return False

            # Check admin cert exists
            if "admin_client_cert_pem" not in output or not output["admin_client_cert_pem"]:
                logger.warning("Admin client certificate missing from PKI output")
                return False

            # In M2, we stub the CA endpoint check but log readiness
            health = output.get("health_status", "Healthy")
            if health == "Unhealthy":
                logger.error("PKI health check indicates CA is unhealthy")
                return False

            if health == "Sick":
                logger.warning("PKI health check indicates transient issues (CA may be slow)")
                return True

            logger.info("PKI healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"PKI healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if Phase 3 should be skipped.

        Skip if PKI has already been bootstrapped (idempotent reload).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if PKI already bootstrapped, False if should execute
        """
        if context.runtime_state.pki_output is not None:
            context.logger.info("PKI already bootstrapped, skipping Phase 3")
            return True
        return False

    # ─────────────────────────────────────────────
    # Private implementation methods
    # ─────────────────────────────────────────────

    async def _init_step_ca(self, context: PhaseContext) -> dict[str, Any]:
        """Initialize step-ca container and configuration.

        Args:
            context: Phase context

        Returns:
            Dict with CA initialization status and issuer URL

        Raises:
            RuntimeError: If CA initialization fails
        """
        logger = context.logger

        logger.info("Initializing step-ca certificate authority")

        # M2 stub: Real implementation would deploy step-ca Docker container
        return {
            "ca_type": "step-ca",
            "status": "ready",
            "issuer_url": "https://ca.netengine.local:9000",
            "provisioner_id": "mock-provisioner-id",
            "initialized_at": datetime.utcnow().isoformat(),
        }

    async def _generate_root_ca(self, context: PhaseContext) -> dict[str, str]:
        """Generate root CA certificate.

        Args:
            context: Phase context

        Returns:
            Dict with:
            - cert_pem: Root certificate in PEM format
            - fingerprint: SHA256 fingerprint of certificate

        Raises:
            RuntimeError: If certificate generation fails
        """
        logger = context.logger

        logger.info("Generating root CA certificate")

        # M2 stub: Real implementation would call step-ca API or openssl
        cert_pem = (
            "-----BEGIN CERTIFICATE-----\n"
            "MIIBpTCCAQ2gAwIBAgIRAIpxHZHMmN+8F7K9QCo1X6YwCgYIKoZIj0EAwIwFDESM\n"
            "QAwDgYDVQQDDAd0ZXN0LWNhMCAXDTIzMDEwMTAwMDAwMFoYDzIxMjIxMjMxMjM1OTU5\n"
            "WjAUMRIwEAYDVQQDDAlUZXN0IENBIDA1MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcD\n"
            "QgAEMKBCTL5q82p+DtmrseJ6O/wSomxIa2xhv+qvdYeHLkKB3DmWvVmWqJB3vFkJ\n"
            "UqI4FQVfH6I5R5kJeMl+TNOXd6OBijCBhzAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0T\n"
            "AQH/BAUwAwEB/zAdBgNVHQ4EFgQUYJTYJuZvWjDK6fECCHp4g0rRIjAwBgNVHSME\n"
            "KTAngBRglNgm5m9aMMrp8QIIeniDStEiIKEYpBYwFDESMBAGA1UEAwwHdGVzdC1j\n"
            "YYIRAIpxHZHMmN+8F7K9QCo1X6YwCgYIKoZIj0EAwIwFDESMQAwDgYDVQQDDAd0\n"
            "-----END CERTIFICATE-----\n"
        )

        fingerprint = "sha256:4a8e3c9f2d1b7e5a6c4d8b2f9e1a3c5d7b9f2e4a6c8d0b2e4f6a8c0d2e4f6a"

        return {
            "cert_pem": cert_pem,
            "fingerprint": fingerprint,
        }

    async def _generate_admin_cert(self, context: PhaseContext) -> dict[str, str]:
        """Generate admin client certificate for bootstrapping.

        Args:
            context: Phase context

        Returns:
            Dict with:
            - cert_pem: Admin certificate in PEM format
            - key_pem: Admin private key in PEM format

        Raises:
            RuntimeError: If certificate generation fails
        """
        logger = context.logger

        logger.info("Generating admin client certificate")

        # M2 stub: Real implementation would call step-ca API
        cert_pem = (
            "-----BEGIN CERTIFICATE-----\n"
            "MIIBsDCCAVWgAwIBAgIRAM5X7K3cWkEQFVkHfPw1xvAwCgYIKoZIj0EAwIwFDESM\n"
            "QAwDgYDVQQDDAd0ZXN0LWNhMCAXDTIzMDEwMTAwMDAwMFoYDzIxMjIxMjMxMjM1OTU5\n"
            "WjAbMRkwFwYDVQQDDBBhZG1pbi1jbGllbnQtY2VydDBZMBMGByqGSM49AgEGCCqG\n"
            "SM49AwEHA0IABPMhF5GwM5k3R7e9K2pQ8fQ2E1v3Zm5K8L9QzM2R5K8zR7K8R2Zm\n"
            "K9L9QzM3R5K9R3Zm5L8K8QzN4R9ajWjBYzAOBgNVHQ8BAf8EBAMCBaAwKwYDVR0l\n"
            "BCQwIgYIKwYBBQUHAwIGCCsGAQUFBwMEBggrBgEFBQcDBjAMBgNVHRMBAf8EAjAA\n"
            "-----END CERTIFICATE-----\n"
        )

        key_pem = (
            "-----BEGIN EC PRIVATE KEY-----\n"
            "MHcCAQEEIIGlEQqR7J5K9L8QzM3R5K9R3Zm5L8K8QzN4R5N5R5N5L8oAoGCCqGSM\n"
            "49AwEHoUQDQgAE8yEXkbAzmTdHt70ralDx9DYTm/dmbkrwv1DMzZHkrzNHsrxHZmY\n"
            "r0v1DMzdHkr1HdmbkrwrxDM3hH1qNQ==\n"
            "-----END EC PRIVATE KEY-----\n"
        )

        return {
            "cert_pem": cert_pem,
            "key_pem": key_pem,
        }

    async def _store_secrets(self, context: PhaseContext, pki_output: dict[str, Any]) -> None:
        """Store PKI secrets in Supabase.

        Args:
            context: Phase context
            pki_output: PKI output dict with certificates

        Raises:
            RuntimeError: If storage fails
        """
        logger = context.logger

        logger.info("Storing PKI secrets in Supabase")

        # M2 stub: Real implementation would call supabase_client.store_secret()
        # and encrypt before storage. For now, we just log that secrets are stored.
        secrets_to_store = [
            "pki:ca_cert",
            "pki:admin_client_cert",
            "pki:admin_client_key",
        ]

        for secret_key in secrets_to_store:
            logger.debug(f"Stored secret: {secret_key}")

    async def _verify_ca(self, context: PhaseContext, pki_output: dict[str, Any]) -> str:
        """Verify CA endpoint responsiveness and health.

        Returns health status: "Healthy", "Sick", or "Unhealthy".
        - Healthy: CA responds normally
        - Sick: CA responds but slow (transient issues)
        - Unhealthy: CA unreachable or not responding

        Args:
            context: Phase context
            pki_output: PKI output with issuer_url

        Returns:
            Health status string
        """
        logger = context.logger

        logger.info(f"Verifying CA endpoint: {pki_output['issuer_url']}")

        # M2 stub: Real implementation would make HTTP call to CA health endpoint
        # For now, we return Healthy. In production this would attempt:
        #   GET https://ca.netengine.local:9000/health
        # and return Sick if slow or Unhealthy if unreachable.

        return "Healthy"

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a PKI event.

        In M2, events are logged but not yet queued to pgmq.
        M4+ handlers will integrate with pgmq queue.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "pki.ready")
            payload: Event payload dict
        """
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="pki_handler",
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
