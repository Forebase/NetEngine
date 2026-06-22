import asyncio
import ipaddress
import logging
from typing import Any, Dict

from netengine.core.pgmq_client import PGMQClient
from netengine.core.supabase_client import get_supabase
from netengine.events.schema import EventEnvelope
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.domain_registry_handler import DomainRegistryHandler
from netengine.handlers.gateway_handler import GatewayHandler


class ANDHandler:
    def __init__(self, docker: DockerHandler, state, context: PhaseContext | None = None):
        self.context = context or PhaseContext(
            spec={}, runtime_state=state, logger=logging.getLogger(__name__)
        )
        self.docker = docker
        self.state = state
        self.domain_registry = DomainRegistryHandler()
        self.gateway = GatewayHandler(docker)
        self.dns = DNSHandler()
        self.supabase = get_supabase()
        self.pgmq = PGMQClient()

    async def provision_and(self, and_name: str, org: str, profile: str, dns_suffix: str) -> None:
        """Full AND provisioning: bridge, address, gateway attach, rules, DNS."""
        # 1. Allocate subnet from Domain Registry
        cidr = await self.domain_registry.allocate_address(and_name, profile)
        # 2. Create isolated Docker bridge
        bridge_name = f"netengines_and_{and_name}"
        await self.docker.create_network(
            name=bridge_name, driver="bridge", subnet=cidr, internal=True  # no NAT by default
        )
        # 3. Attach gateway to bridge
        gateway_ip = str(ipaddress.ip_network(cidr).network_address + 1)  # first usable IP
        await self.docker.connect_network(
            container=self.gateway.gateway_container, network=bridge_name, ip=gateway_ip
        )
        # 4. Generate and apply nftables rules
        rules = await self.gateway.generate_rules(and_name, profile, cidr)
        await self.gateway.apply_rules(and_name, rules)
        # 5. Register DNS suffix zone
        await self.dns.add_zone_record(
            context=self.context,
            zone=dns_suffix,
            record_type="A",
            name="@",
            value=gateway_ip,
            ttl=300,
        )
        # 6. Update state in Supabase
        await self.supabase.table("address_leases").upsert(
            {
                "and_name": and_name,
                "cidr": cidr,
                "gateway_ip": gateway_ip,
                "profile": profile,
                "dns_suffix": dns_suffix,
            }
        ).execute()
        # 7. Emit event for downstream (could trigger service deployments)
        await self.pgmq.send(
            "and_provisioned",
            EventEnvelope.create(
                event_type="and.provisioned",
                emitted_by="and_handler",
                payload={"and_name": and_name, "org": org, "profile": profile, "cidr": cidr},
            ),
        )

    async def update_and_profile(self, and_name: str, new_profile: str) -> None:
        """Change an AND's profile: regenerate rules and reload atomically."""
        # Fetch current cidr from state
        result = (
            await self.supabase.table("address_leases")
            .select("cidr")
            .eq("and_name", and_name)
            .execute()
        )
        if not result.data:
            raise RuntimeError(f"AND {and_name} not found")
        cidr = result.data[0]["cidr"]
        rules = await self.gateway.generate_rules(and_name, new_profile, cidr)
        await self.gateway.apply_rules(and_name, rules)
        # Update profile in state
        await self.supabase.table("address_leases").update({"profile": new_profile}).eq(
            "and_name", and_name
        ).execute()

    async def deprovision_and(self, and_name: str) -> None:
        """Remove AND: delete rules, detach gateway, remove bridge."""
        # 1. Remove nftables rules
        await self.gateway.remove_rules(and_name)
        # 2. Detach gateway from bridge
        bridge_name = f"netengines_and_{and_name}"
        await self.docker.disconnect_network(
            container=self.gateway.gateway_container, network=bridge_name
        )
        # 3. Remove bridge
        await self.docker.remove_network(bridge_name)
        # 4. Remove lease from Supabase
        await self.supabase.table("address_leases").delete().eq("and_name", and_name).execute()
        # 5. Remove DNS suffix
        # (optional)
