# netengine/handlers/pki_phase.py (or phases/phase_pki.py)
from datetime import datetime
from typing import Any

from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.workers.pki_cert_rotation_worker import CertTypeRotationConfig, PKICertRotationWorker
from netengine.logging import get_logger

logger = get_logger(__name__)


class PKIPhaseHandler(BasePhaseHandler):
    """Phase 3: PKI + ACME bootstrap."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec

        logger.info("Starting Phase 3: PKI + ACME")

        if context.mock_mode:
            # Stub output — no real container operations
            context.runtime_state.pki_output = {
                "ca_ip": spec.pki.acme.listen_ip,
                "ca_dns": spec.pki.acme.canonical_name,
                "bootstrapped": True,
                "mock": True,
                "deployed_at": datetime.utcnow().isoformat(),
            }
            context.runtime_state.pki_bootstrapped = True
            context.runtime_state.phase_completed["3"] = True
            context.runtime_state.completed_at = datetime.utcnow()
            context.runtime_state.save()
            logger.info("Phase 3: PKI + ACME complete (mock mode)")
            await self._emit_event(
                context,
                event_type="pki.ready",
                payload={"ca_ip": spec.pki.acme.listen_ip, "ca_dns": spec.pki.acme.canonical_name},
            )
            return

        # Use docker_client from context, falling back to a new DockerHandler
        docker = context.docker_client if context.docker_client is not None else DockerHandler()
        pki = PKIHandler(docker, context.runtime_state, spec)

        # 1. Bootstrap CA (generate + start server)
        await pki.bootstrap()

        # 2. Register DNS record for ca.platform.internal
        from netengine.handlers.dns import DNSHandler

        dns_handler = DNSHandler()
        await dns_handler.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="ca",
            value=pki.ca_ip,
            ttl=300,
        )

        # 3. Persist outputs
        context.runtime_state.pki_output = {
            "ca_ip": pki.ca_ip,
            "ca_dns": pki.ca_dns,
            "container_id": context.runtime_state.step_ca_container_id,
            "bootstrapped": True,
            "deployed_at": datetime.utcnow().isoformat(),
        }
        context.runtime_state.pki_bootstrapped = True
        context.runtime_state.phase_completed["3"] = True
        context.runtime_state.completed_at = datetime.utcnow()
        context.runtime_state.save()

        logger.info("Phase 3: PKI + ACME complete")

        # Register certificate rotation worker
        self._register_rotation_worker(context, pki, spec)

        await self._emit_event(
            context, event_type="pki.ready", payload={"ca_ip": pki.ca_ip, "ca_dns": pki.ca_dns}
        )

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Check PKI health."""
        if context.mock_mode:
            return context.runtime_state.pki_output is not None
        try:
            docker = context.docker_client if context.docker_client is not None else DockerHandler()
            pki = PKIHandler(docker, context.runtime_state, context.spec)
            return await pki.healthcheck()
        except Exception as exc:
            logger.warning(f"PKI phase healthcheck error: {exc}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Skip if already bootstrapped."""
        return context.runtime_state.phase_completed.get("3", False)

    def _register_rotation_worker(self, context: PhaseContext, pki: PKIHandler, spec: Any) -> None:
        """Register certificate rotation worker with ConsumerSupervisor."""
        if not context.consumer_supervisor:
            return

        policy = spec.pki.rotation_policy
        if not policy.enabled:
            context.logger.info("PKI certificate rotation disabled")
            return

        # Build rotation configs from spec (with defaults)
        rotation_configs = []

        # Platform identity cert
        platform_cfg_dict = policy.cert_type_overrides.get("platform_identity")
        if platform_cfg_dict and isinstance(platform_cfg_dict, dict):
            platform_interval = platform_cfg_dict.get(
                "rotation_interval_hours", policy.default_interval_hours
            )
            platform_warning = platform_cfg_dict.get(
                "expiry_warning_days", policy.default_warning_days
            )
            platform_cfg = CertTypeRotationConfig(
                cert_type="platform_identity",
                rotation_interval_hours=platform_interval,
                expiry_warning_days=platform_warning,
                rotation_callback=self._prepare_app_cert_rotation,
            )
        else:
            platform_cfg = CertTypeRotationConfig(
                cert_type="platform_identity",
                rotation_interval_hours=policy.default_interval_hours,
                expiry_warning_days=policy.default_warning_days,
                rotation_callback=self._prepare_app_cert_rotation,
            )
        rotation_configs.append(platform_cfg)

        # Inworld identity cert
        inworld_cfg_dict = policy.cert_type_overrides.get("inworld_identity")
        if inworld_cfg_dict and isinstance(inworld_cfg_dict, dict):
            inworld_interval = inworld_cfg_dict.get(
                "rotation_interval_hours", policy.default_interval_hours
            )
            inworld_warning = inworld_cfg_dict.get(
                "expiry_warning_days", policy.default_warning_days
            )
            inworld_cfg = CertTypeRotationConfig(
                cert_type="inworld_identity",
                rotation_interval_hours=inworld_interval,
                expiry_warning_days=inworld_warning,
                rotation_callback=self._prepare_app_cert_rotation,
            )
        else:
            inworld_cfg = CertTypeRotationConfig(
                cert_type="inworld_identity",
                rotation_interval_hours=policy.default_interval_hours,
                expiry_warning_days=policy.default_warning_days,
                rotation_callback=self._prepare_app_cert_rotation,
            )
        rotation_configs.append(inworld_cfg)

        # App certs (with rotation callback for graceful transition)
        app_cfg_dict = policy.cert_type_overrides.get("app")
        if app_cfg_dict and isinstance(app_cfg_dict, dict):
            app_interval = app_cfg_dict.get(
                "rotation_interval_hours", policy.default_interval_hours
            )
            app_warning = app_cfg_dict.get("expiry_warning_days", policy.default_warning_days)
            app_cfg = CertTypeRotationConfig(
                cert_type="app",
                rotation_interval_hours=app_interval,
                expiry_warning_days=app_warning,
                rotation_callback=self._prepare_app_cert_rotation,
            )
        else:
            app_cfg = CertTypeRotationConfig(
                cert_type="app",
                rotation_interval_hours=policy.default_interval_hours,
                expiry_warning_days=policy.default_warning_days,
                rotation_callback=self._prepare_app_cert_rotation,
            )
        rotation_configs.append(app_cfg)

        # Storage/MinIO cert
        storage_cfg_dict = policy.cert_type_overrides.get("storage")
        if storage_cfg_dict and isinstance(storage_cfg_dict, dict):
            storage_interval = storage_cfg_dict.get(
                "rotation_interval_hours", policy.default_interval_hours
            )
            storage_warning = storage_cfg_dict.get(
                "expiry_warning_days", policy.default_warning_days
            )
            storage_cfg = CertTypeRotationConfig(
                cert_type="storage",
                rotation_interval_hours=storage_interval,
                expiry_warning_days=storage_warning,
            )
        else:
            storage_cfg = CertTypeRotationConfig(
                cert_type="storage",
                rotation_interval_hours=policy.default_interval_hours,
                expiry_warning_days=policy.default_warning_days,
            )
        rotation_configs.append(storage_cfg)

        # Register rotation worker
        if context.pgmq_client:
            rotation_worker = PKICertRotationWorker(pki, context.pgmq_client, rotation_configs)
            context.consumer_supervisor.register("pki_cert_rotation", rotation_worker.run)
            context.logger.info("PKI certificate rotation worker registered")

    async def _prepare_app_cert_rotation(self, cn: str, cert_metadata: dict) -> None:
        """Called before rotating app cert - prepare for transition.

        This is a hook point for future graceful transition logic.
        When rotation occurs, a new version of the cert is issued.
        """
        current_version = cert_metadata.get("version", 1)
        logger.info(
            "preparing_app_cert_rotation", extra={"cn": cn, "current_version": current_version}
        )

    async def _emit_event(self, context, event_type, payload):
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="pki_phase",
            payload=payload,
            correlation_id=getattr(context.runtime_state, "correlation_id", None),
            parent_event_id=getattr(context.runtime_state, "parent_event_id", None),
        )
        context.logger.info(f"Event emitted: {event_type}")
        if context.pgmq_client is not None:
            try:
                await context.pgmq_client.send(event)
            except Exception as exc:
                context.logger.warning(f"Failed to queue pki event: {exc}")
