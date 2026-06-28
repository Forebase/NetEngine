# netengine/handlers/pki_phase.py (or phases/phase_pki.py)
from datetime import UTC, datetime
from typing import Any

from netengine.events.queues import queue_for_event_type
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.logging import get_logger
from netengine.workers.pki_cert_rotation_worker import CertTypeRotationConfig, PKICertRotationWorker

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
                "deployed_at": datetime.now(UTC).isoformat(),
            }
            context.runtime_state.pki_bootstrapped = True
            context.runtime_state.phase_completed["3"] = True
            context.runtime_state.completed_at = datetime.now(UTC)
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

        # 3. Setup optional PKI features
        pki_output: dict = {
            "ca_ip": pki.ca_ip,
            "ca_dns": pki.ca_dns,
            "container_id": context.runtime_state.step_ca_container_id,
            "bootstrapped": True,
            "deployed_at": datetime.now(UTC).isoformat(),
        }

        if spec.pki.crl_enabled:
            pki_output["crl_url"] = f"https://{pki.ca_ip}/1.0/crl"
            pki_output["crl_enabled"] = True
            logger.info(f"CRL endpoint: {pki_output['crl_url']}")

        if spec.pki.ocsp_enabled:
            pki_output["ocsp_url"] = f"https://{pki.ca_ip}/ocsp"
            pki_output["ocsp_enabled"] = True
            logger.info(f"OCSP endpoint: {pki_output['ocsp_url']}")

        if spec.pki.intermediate_ca_enabled:
            pki_output["intermediate_ca_enabled"] = True
            if context.runtime_state.intermediate_ca_cert:
                pki_output["intermediate_ca_cert_available"] = True
                pki_output["intermediate_ca_cert"] = context.runtime_state.intermediate_ca_cert
            logger.info("Intermediate CA enabled and tracked in state")

        if spec.pki.dnssec_enabled:
            dnssec_info = await pki.setup_dnssec(
                zone="internal",
                ksk_lifetime_days=spec.pki.dnssec_ksk_lifetime_days,
                zsk_lifetime_days=spec.pki.dnssec_zsk_lifetime_days,
            )
            context.runtime_state.dnssec_output = dnssec_info
            pki_output["dnssec_enabled"] = True
            pki_output["dnssec_zone"] = dnssec_info["zone"]
            pki_output["dnssec_ksk"] = dnssec_info["ksk_name"]
            pki_output["dnssec_zsk"] = dnssec_info["zsk_name"]
            logger.info(f"DNSSEC keys generated for zone: {dnssec_info['zone']}")

        # 4. Persist outputs
        context.runtime_state.pki_output = pki_output
        context.runtime_state.pki_bootstrapped = True
        context.runtime_state.phase_completed["3"] = True
        context.runtime_state.completed_at = datetime.now(UTC)
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
        """Register certificate rotation worker with ConsumerSupervisor.

        Builds rotation configs for all cert types: the four well-known built-in
        types (platform_identity, inworld_identity, app, storage) plus any
        additional types declared in policy.cert_type_overrides.
        """
        if not context.consumer_supervisor:
            return

        policy = spec.pki.rotation_policy
        if not policy.enabled:
            context.logger.info("PKI certificate rotation disabled")
            return

        # Cert types that receive the graceful-transition rotation callback
        _callback_types = {"platform_identity", "inworld_identity", "app"}

        # Merge built-in cert types with any extras declared in spec overrides
        _builtin = ["platform_identity", "inworld_identity", "app", "storage"]
        _extra = [t for t in policy.cert_type_overrides if t not in _builtin]
        all_cert_types = _builtin + _extra

        rotation_configs = []
        for cert_type in all_cert_types:
            override = policy.cert_type_overrides.get(cert_type)
            if isinstance(override, dict):
                interval = override.get("rotation_interval_hours", policy.default_interval_hours)
                warning = override.get("expiry_warning_days", policy.default_warning_days)
            else:
                interval = policy.default_interval_hours
                warning = policy.default_warning_days

            rotation_configs.append(
                CertTypeRotationConfig(
                    cert_type=cert_type,
                    rotation_interval_hours=interval,
                    expiry_warning_days=warning,
                    rotation_callback=(
                        self._prepare_app_cert_rotation if cert_type in _callback_types else None
                    ),
                )
            )

        if context.pgmq_client:
            rotation_worker = PKICertRotationWorker(pki, context.pgmq_client, rotation_configs)
            context.consumer_supervisor.register("pki_cert_rotation", rotation_worker.run)
            context.logger.info(
                f"PKI certificate rotation worker registered ({len(rotation_configs)} cert types)"
            )

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
                await context.pgmq_client.send(queue_for_event_type(event_type), event)
            except Exception as exc:
                context.logger.warning(f"Failed to queue pki event: {exc}")
