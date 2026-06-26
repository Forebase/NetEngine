# netengine/handlers/pki_phase.py (or phases/phase_pki.py)
from datetime import datetime
from typing import Any

from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.pki_handler import PKIHandler
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
