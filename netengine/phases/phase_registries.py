import asyncio
from datetime import datetime

from netengine.core.pgmq_client import PGMQClient
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.domain_registry_handler import DomainRegistryHandler
from netengine.handlers.whois_server import WHOISServer
from netengine.handlers.world_registry_handler import WorldRegistryHandler


class RegistriesPhaseHandler(BasePhaseHandler):
    """Phase 5: World Registry + Domain Registry + WHOIS + Event wiring."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec

        logger.info("Phase 5: Registries + event wiring")

        # 1. Seed World Registry
        world = WorldRegistryHandler()
        await world.seed_from_spec(spec)

        # 2. Seed Domain Registry (address pools)
        domain = DomainRegistryHandler()
        await domain.seed_address_pools(spec)

        # 3. Start WHOIS server (in a background task)
        whois = WHOISServer()
        asyncio.create_task(whois.start())

        # 4. Register TLD delegations from spec
        tlds = spec.get("domain_registry", {}).get("tld_delegations", [])
        dns = DNSHandler()  # or get from context
        for tld in tlds:
            # Add NS records to root zone
            await dns.add_zone_record(
                zone="root.internal", record_type="NS", name=tld["name"], value=tld["ns_server"]
            )
            # Add A record for the TLD's NS server
            await dns.add_zone_record(
                zone="root.internal", record_type="A", name=tld["ns_server"], value=tld["listen_ip"]
            )

        # 5. Wire pgmq consumers (stub – in production, run a loop)
        # We'll set up a consumer for DNS updates in a background task.
        asyncio.create_task(self._consume_dns_updates(context))

        # 6. Update state
        context.runtime_state.phase_completed["5"] = True
        context.runtime_state.save()
        logger.info("Phase 5 complete")

    async def _consume_dns_updates(self, context: PhaseContext):
        """pgmq consumer: domain.registered -> DNSHandler.add_zone_record."""
        pgmq = PGMQClient()
        dns = DNSHandler()
        while True:
            msg = await pgmq.receive("dns_updates")
            if not msg:
                await asyncio.sleep(1)
                continue
            try:
                envelope = EventEnvelope(**json.loads(msg["message"]))
                payload = envelope.payload
                # Add zone record for the domain (e.g., acme.internal -> IP)
                # For MVP, we add an A record pointing to a placeholder or to the AND gateway.
                # In real use, the IP would come from the AND allocation.
                await dns.add_zone_record(
                    zone=payload["domain"],
                    record_type="A",
                    name="@",
                    value="10.0.0.1",  # placeholder – would be replaced with actual IP from AND handler
                )
                await pgmq.delete("dns_updates", msg["msg_id"])
            except Exception as e:
                await pgmq.archive_to_dlq("dns_updates", msg["msg_id"], str(e))
