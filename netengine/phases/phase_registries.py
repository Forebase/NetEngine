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

        # 3. Start WHOIS server (in a background task)
        whois = WHOISServer()
        asyncio.create_task(whois.start())

        # 4. Register TLD delegations from spec
        tlds = spec.get("domain_registry", {}).get("tld_delegations", [])
        dns = DNSHandler()  # or get from context
        for tld in tlds:
            # Add NS records to root zone
            await dns.add_zone_record(
                context=context,
                zone="root.internal",
                record_type="NS",
                name=tld["name"],
                value=tld["ns_server"],
            )
            # Add A record for the TLD's NS server
            await dns.add_zone_record(
                context=context,
                zone="root.internal",
                record_type="A",
                name=tld["ns_server"],
                value=tld["listen_ip"],
            )

        # 5. Wire pgmq consumers
        # Register consumer with supervisor to run after all phases complete
        if context.consumer_supervisor:
            # Create a wrapper that captures the context
            async def dns_consumer():
                await self._consume_dns_updates(context)

            context.consumer_supervisor.register("dns_updates", dns_consumer)

        # 6. Update state
        context.runtime_state.world_registry_output = {
            "seeded": True,
            "deployed_at": datetime.utcnow().isoformat(),
        }
        context.runtime_state.domain_registry_output = {
            "address_pools_seeded": True,
            "tld_delegations": tlds,
            "deployed_at": datetime.utcnow().isoformat(),
        }
        context.runtime_state.phase_completed["5"] = True
        context.runtime_state.save()
        logger.info("Phase 5 complete")

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Check if registries are healthy."""
        try:
            # Verify World Registry is accessible
            world = WorldRegistryHandler()
            # Try to query the registry (basic healthcheck)
            supabase = __import__(
                "netengine.core.supabase_client", fromlist=["get_supabase"]
            ).get_supabase()
            result = await supabase.table("world_registry").select("*").limit(1).execute()
            return result.status_code == 200 if hasattr(result, "status_code") else True
        except Exception:
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Skip if Phase 5 already completed."""
        return context.runtime_state.phase_completed.get("5", False)

    async def _consume_dns_updates(self, context: PhaseContext):
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

                    # Add zone record for the domain (e.g., acme.internal -> IP)
                    # For MVP, we add an A record pointing to a placeholder or to the AND gateway.
                    # In real use, the IP would come from the AND allocation.
                    await dns.add_zone_record(
                        context=context,
                        zone=payload["domain"],
                        record_type="A",
                        name="@",
                        value="10.0.0.1",  # placeholder – would be replaced with actual IP from AND handler
                    )
                    await pgmq.delete("dns_updates", msg["msg_id"])
                    logger.info(f"Successfully processed DNS update for domain: {payload.get('domain')}")
                except Exception as e:
                    logger.error(f"Error processing DNS update: {e}")
                    await pgmq.archive_to_dlq("dns_updates", msg["msg_id"], str(e))
            except Exception as e:
                logger.error(f"Error in DNS update consumer loop: {e}")
                await asyncio.sleep(5)  # backoff before retrying
