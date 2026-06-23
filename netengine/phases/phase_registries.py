import asyncio
import json
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

        # 3. Start WHOIS server via ConsumerSupervisor so crashes are visible
        #    and the task is gracefully shut down with the rest of the system.
        whois = WHOISServer()
        context.consumer_supervisor.register(  # type: ignore[union-attr]
            "whois_server", whois.start
        )

        # 4. Register TLD delegations from spec
        tlds = spec.dns.tlds if spec.dns else []
        dns = DNSHandler()
        for tld in tlds:
            await dns.add_zone_record(
                context=context,
                zone="root.internal",
                record_type="NS",
                name=tld.name,
                value=f"ns.{tld.name}",
            )
            await dns.add_zone_record(
                context=context,
                zone="root.internal",
                record_type="A",
                name=f"ns.{tld.name}",
                value=tld.listen_ip,
            )

        # 5. Wire pgmq consumer for DNS updates through supervisor
        context.consumer_supervisor.register(  # type: ignore[union-attr]
            "dns_updates",
            lambda: self._consume_dns_updates(context),
        )

        # 6. Update state
        context.runtime_state.world_registry_output = {
            "seeded": True,
            "deployed_at": datetime.utcnow().isoformat(),
        }
        context.runtime_state.domain_registry_output = {
            "address_pools_seeded": True,
            "tld_delegations": [t.model_dump() for t in tlds],
            "deployed_at": datetime.utcnow().isoformat(),
        }
        context.runtime_state.phase_completed["5"] = True
        context.runtime_state.save()
        logger.info("Phase 5 complete")

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Check if registries are healthy."""
        try:
            from netengine.core.supabase_client import get_supabase

            supabase = get_supabase()
            result = supabase.table("world_registry").select("*").limit(1).execute()
            # postgrest-py returns a APIResponse with a .data list; presence of
            # the attribute (not an exception) indicates connectivity.
            return hasattr(result, "data")
        except Exception:
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Skip if Phase 5 already completed."""
        return context.runtime_state.phase_completed.get("5", False)

    async def _consume_dns_updates(self, context: PhaseContext) -> None:
        """pgmq consumer: domain.registered -> DNSHandler.add_zone_record."""
        logger = context.logger
        pgmq = PGMQClient()
        dns = DNSHandler()
        while True:
            try:
                msg = await pgmq.receive("dns_updates")
                if not msg:
                    await asyncio.sleep(1)
                    continue

                try:
                    envelope = EventEnvelope(**json.loads(msg["message"]))
                    payload = envelope.payload
                    logger.info(f"Processing DNS update for domain: {payload.get('domain')}")

                    await dns.add_zone_record(
                        context=context,
                        zone=payload["domain"],
                        record_type="A",
                        name="@",
                        value="10.0.0.1",
                    )
                    await pgmq.delete("dns_updates", msg["msg_id"])
                    logger.info(
                        f"Successfully processed DNS update for domain: {payload.get('domain')}"
                    )
                except Exception as e:
                    logger.error(f"Error processing DNS update: {e}")
                    await pgmq.archive_to_dlq("dns_updates", msg["msg_id"], str(e))
            except Exception as e:
                logger.error(f"Error in DNS update consumer loop: {e}")
                await asyncio.sleep(5)
