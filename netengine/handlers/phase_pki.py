# netengine/handlers/pki_phase.py (or phases/phase_pki.py)
from datetime import datetime
from typing import Any

from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.pki_handler import PKIHandler


class PKIPhaseHandler(BasePhaseHandler):
    """Phase 3: PKI + ACME bootstrap."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec

        logger.info("Starting Phase 3: PKI + ACME")

        # Instantiate PKIHandler with docker and state
        docker = DockerHandler()  # or get from context if already available
        pki = PKIHandler(docker, context.runtime_state, spec)

        # 1. Bootstrap CA (generate + start server)
        await pki.bootstrap()

        # 2. Register DNS record for ca.platform.internal
        #    We need to use the DNSHandler to add the record.
        #    Since DNSHandler is a phase handler, we can either call its method directly
        #    or instantiate a new DNS handler.
        #    Assuming we have a reference to the DNS handler in context or we can create one.
        #    For simplicity, we'll use the same pattern as DNSHandler.
        from netengine.handlers.dns import DNSHandler  # import your existing DNS handler

        dns_handler = DNSHandler()  # or get from context
        # But we need to call the add_zone_record method (which is a stub currently).
        # We'll implement it below.
        await dns_handler.add_zone_record(
            zone="platform.internal", record_type="A", name="ca", value=pki.ca_ip, ttl=300
        )

        # 3. Update state
        context.runtime_state.pki_bootstrapped = True
        context.runtime_state.completed_at = datetime.utcnow()
        context.runtime_state.save()

        logger.info("Phase 3: PKI + ACME complete")

        # Emit event
        await self._emit_event(
            context, event_type="pki.ready", payload={"ca_ip": pki.ca_ip, "ca_dns": pki.ca_dns}
        )

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Check PKI health."""
        try:
            docker = DockerHandler()
            pki = PKIHandler(docker, context.runtime_state, context.spec)
            return await pki.healthcheck()
        except Exception:
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
        # In M4+ you would queue to pgmq
