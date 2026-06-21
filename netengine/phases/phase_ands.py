import asyncio
from datetime import datetime

from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.and_handler import ANDHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.domain_registry_handler import DomainRegistryHandler


class ANDsPhaseHandler(BasePhaseHandler):
    """Phase 7: Administrative Network Domains."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec
        ands_instances = spec.get("ands", {}).get("instances", [])

        logger.info(f"Phase 7: Provisioning {len(ands_instances)} ANDs")

        docker = DockerHandler()
        and_handler = ANDHandler(docker, context.runtime_state)

        # Seed address pools if not already done (Phase 5 should have done it)
        domain_reg = DomainRegistryHandler()
        await domain_reg.seed_address_pools(spec)

        # Provision each AND instance
        for and_conf in ands_instances:
            name = and_conf["name"]
            org = and_conf["org"]
            profile = and_conf.get("profile", "business")
            dns_suffix = and_conf.get("dns_suffix", f"{org}.internal")
            logger.info(f"Provisioning AND: {name} for {org} with profile {profile}")
            await and_handler.provision_and(name, org, profile, dns_suffix)

        # Set up pgmq consumer for future org admissions
        asyncio.create_task(self._consume_org_admissions(context))

        context.runtime_state.phase_completed["7"] = True
        context.runtime_state.save()
        logger.info("Phase 7 complete")

    async def _consume_org_admissions(self, context):
        """Listen for new org.admitted events and provision AND for them."""
        import json

        from netengine.core.pgmq_client import PGMQClient
        from netengine.events.schema import EventEnvelope

        pgmq = PGMQClient()
        docker = DockerHandler()
        and_handler = ANDHandler(docker, context.runtime_state)
        while True:
            msg = await pgmq.receive("and_provisioning")
            if not msg:
                await asyncio.sleep(1)
                continue
            try:
                envelope = EventEnvelope(**json.loads(msg["message"]))
                if envelope.event_type != "org.admitted":
                    await pgmq.delete("and_provisioning", msg["msg_id"])
                    continue
                payload = envelope.payload
                org = payload["org_name"]
                profile = payload.get("and_profile", "business")
                # Generate a unique AND name from org
                and_name = f"{org.replace('_', '-')}-net"
                dns_suffix = f"{org}.internal"
                await and_handler.provision_and(and_name, org, profile, dns_suffix)
                await pgmq.delete("and_provisioning", msg["msg_id"])
            except Exception as e:
                await pgmq.archive_to_dlq("and_provisioning", msg["msg_id"], str(e))
