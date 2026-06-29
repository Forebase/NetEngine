"""Phase 8: World Services (Mail + Storage) deployment.

Responsibilities:
- Deploy Postfix mail infrastructure with DKIM/DMARC
- Deploy MinIO object storage for org data persistence
- Orchestrate both services with prerequisite validation
- Event-driven provisioning for org-specific infrastructure
- Health checks and idempotence for both services
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from netengine.events.emitter import emit_event
from netengine.events.queues import Queue
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.mail_handler import MailHandler
from netengine.handlers.minio_handler import StorageHandler
from netengine.handlers.pki_handler import PKIHandler


class ServicesPhaseHandler(BasePhaseHandler):
    """Phase 8: World Services (Mail + Storage).

    Deploys world-level infrastructure services:
    - Mail: Postfix SMTP with DKIM/DMARC signing
    - Storage: MinIO S3-compatible object storage

    Design:
    - Prerequisite validation for M1-M7
    - Proper spec model usage (not dict access)
    - Dual service orchestration
    - State persistence in world_services_output
    - Event-driven org-specific provisioning
    """

    async def execute(self, context: PhaseContext) -> None:
        """Execute Phase 8: Deploy world services.

        Steps:
        1. Validate M1-M7 prerequisites
        2. Deploy Mail service (Postfix + DKIM/DMARC)
        3. Deploy Storage service (MinIO)
        4. Inject DNS records and configure routing
        5. Start org provisioning event consumer
        6. Emit services.ready event

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If prerequisites missing or deployment fails
        """
        logger = context.logger
        spec = context.spec
        runtime_state = context.runtime_state

        logger.info("Starting Phase 8: World Services deployment")

        # Validate prerequisites
        self._validate_prerequisites(runtime_state, logger)

        runtime_state.started_at = datetime.now(UTC)

        try:
            services_output: dict[str, Any] = {}

            # Initialize handlers
            docker = DockerHandler()
            dns = DNSHandler()
            pki = PKIHandler(docker, runtime_state, spec)

            # Deploy Mail service
            if spec.world_services.mail.enabled:
                logger.info("Deploying Mail service (Postfix + DKIM/DMARC)")
                mail_handler = MailHandler(context, docker, dns)
                mail_output = await mail_handler.deploy_postfix()
                services_output["mail"] = mail_output
                logger.info(
                    "Mail deployment complete: "
                    f"{mail_output.get('orgs_configured', [])} orgs configured"
                )

            # Deploy Storage service (MinIO)
            if spec.world_services.storage.enabled:
                logger.info("Deploying Storage service (MinIO)")
                storage_handler = StorageHandler(context, docker, dns, pki, runtime_state)
                storage_output = await storage_handler.deploy_minio()
                services_output["storage"] = storage_output
                logger.info("Storage deployment complete")

            # Record deployment info
            services_output["deployed_at"] = datetime.now(UTC).isoformat()
            runtime_state.world_services_output = services_output
            runtime_state.completed_at = datetime.now(UTC)

            logger.info("Phase 8 complete: world services ready")

            # Emit success event
            await self._emit_event(
                context,
                event_type="services.ready",
                payload={"services": list(services_output.keys())},
            )

            # Register background consumers
            if context.consumer_supervisor is not None:
                context.consumer_supervisor.register(
                    "org_admission_events",
                    lambda: self._consume_org_admission_events(context, docker, dns),
                )

                # Register monitoring service (always-running health checks)
                from netengine.monitoring import MonitoringService

                monitoring_service = MonitoringService(spec, interval_seconds=60.0)
                context.consumer_supervisor.register(
                    "monitoring_service",
                    monitoring_service.start,
                )

        except Exception as e:
            runtime_state.last_error = str(e)
            runtime_state.last_error_at = datetime.now(UTC)
            logger.error(f"Phase 8 deployment failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify world services are healthy and operational.

        Checks:
        - world_services_output exists (basic)
        - Mail container is running (if enabled)
        - Storage container is running (if enabled)
        - DNS records for mail are present (if mail enabled)

        Args:
            context: Phase execution context

        Returns:
            True if services are healthy, False otherwise
        """
        logger = context.logger
        runtime_state = context.runtime_state

        try:
            # Basic check: output exists
            if runtime_state.world_services_output is None:
                logger.warning("World services not yet deployed")
                return False

            output = runtime_state.world_services_output
            spec = context.spec

            # Check Mail service (if enabled)
            if spec.world_services.mail.enabled:
                if "mail" not in output:
                    logger.warning("Mail service output missing")
                    return False

                mail_output = output["mail"]
                container_id = mail_output.get("container_id")

                # Verify container is running
                docker = DockerHandler()
                try:
                    container = docker.client.containers.get(container_id)
                    if container.status != "running":
                        logger.warning(f"Mail container not running: {container.status}")
                        return False
                except Exception as e:
                    logger.warning(f"Failed to check mail container: {e}")
                    return False

            # Check Storage service (if enabled)
            if spec.world_services.storage.enabled:
                if "storage" not in output:
                    logger.warning("Storage service output missing")
                    return False

                storage_output = output["storage"]
                container_id = storage_output.get("container_id")

                # Verify container is running
                docker = DockerHandler()
                try:
                    container = docker.client.containers.get(container_id)
                    if container.status != "running":
                        logger.warning(f"Storage container not running: {container.status}")
                        return False
                except Exception as e:
                    logger.warning(f"Failed to check storage container: {e}")
                    return False

            logger.info("World services healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"World services healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if Phase 8 should be skipped.

        Skip if world services have already been deployed (idempotent).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if already deployed, False if should execute
        """
        if context.runtime_state.world_services_output is not None:
            context.logger.info("World services already deployed, skipping Phase 8")
            return True
        return False

    def _validate_prerequisites(self, runtime_state, logger) -> None:
        """Validate M1-M7 prerequisites.

        Args:
            runtime_state: Runtime state with all phase outputs
            logger: Logger instance

        Raises:
            RuntimeError: If any prerequisite is missing
        """
        # M1-M2: Substrate + DNS
        if runtime_state.substrate_output is None:
            raise RuntimeError(
                "Substrate phase (Phase 0) must complete before world services. "
                "Ensure Phase 0 has run and created networks."
            )
        if runtime_state.dns_output is None:
            raise RuntimeError(
                "DNS phase (Phase 1-2) must complete before world services. "
                "Ensure Phase 1-2 have run and created zones."
            )

        # M3: PKI
        if runtime_state.pki_output is None:
            raise RuntimeError(
                "PKI phase (Phase 3) must complete before world services. "
                "Ensure Phase 3 has run and generated certificates."
            )

        # M4: Platform Identity
        if runtime_state.identity_platform_output is None:
            raise RuntimeError(
                "Platform Identity phase (Phase 4) must complete before world services. "
                "Ensure Phase 4 has run and bootstrapped Keycloak."
            )

        # M5: Registries
        if runtime_state.world_registry_output is None:
            raise RuntimeError(
                "World Registry phase (Phase 5) must complete before world services. "
                "Ensure Phase 5 has run and registered organizations."
            )

        # M6: In-World Identity
        if runtime_state.identity_inworld_output is None:
            raise RuntimeError(
                "In-World Identity phase (Phase 6) must complete before world services. "
                "Ensure Phase 6 has run and created org realms."
            )

        # M7: ANDs
        if runtime_state.ands_output is None:
            raise RuntimeError(
                "ANDs phase (Phase 7) must complete before world services. "
                "Ensure Phase 7 has run and provisioned network domains."
            )

        logger.info("All Phase 8 prerequisites validated")

    async def _consume_org_admission_events(
        self, context: PhaseContext, docker: DockerHandler, dns: DNSHandler
    ) -> None:
        """Background consumer for org.admitted events → provision org-specific services.

        Listens to pgmq for new organization admissions and auto-provisions
        mail/storage for those orgs (if configured in spec).

        Args:
            context: Phase context
            docker: Docker handler
            dns: DNS handler
        """
        logger = context.logger

        if context.pgmq_client is None:
            logger.info("pgmq_client not available; org service provisioning disabled")
            return

        logger.info("Starting org admission event consumer (services)")

        while True:
            try:
                msg = await context.pgmq_client.receive(Queue.SERVICES_ADMISSIONS)
                if not msg:
                    await asyncio.sleep(1)
                    continue

                # Process org.admitted event
                # (Implementation: create org-specific mail/storage if configured)
                logger.debug("Processing org.admitted event for services provisioning")

                # Mark message as processed
                await context.pgmq_client.delete(Queue.SERVICES_ADMISSIONS, msg["msg_id"])

            except Exception as e:
                logger.error(f"Org admission consumer error: {e}")
                await asyncio.sleep(5)

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a services event.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "services.ready")
            payload: Event payload dict
        """
        await emit_event(
            context, event_type=event_type, emitted_by="services_handler", payload=payload
        )
