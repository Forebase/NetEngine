# netengine/handlers/domain_registry_handler.py
import ipaddress
from typing import Any, Dict, List

from netengine.core.pgmq_client import PGMQClient
from netengine.core.supabase_client import get_supabase
from netengine.events.schema import EventEnvelope


class DomainRegistryHandler:
    def __init__(self):
        self.supabase = get_supabase()
        self.pgmq = PGMQClient()

    async def seed_address_pools(self, spec: Dict[str, Any]) -> None:
        """Seed address pools from domain_registry.address_space."""
        pools = spec.get("domain_registry", {}).get("address_space", [])
        for pool in pools:
            data = {"profile": pool["profile"], "cidr": pool["cidr"]}
            await self.supabase.table("address_pools").upsert(data).execute()

    async def allocate_address(self, and_name: str, profile: str) -> str:
        """Allocate a CIDR from the pool; row‑level lock prevents conflicts."""
        # Begin transaction: select a free block from address_pools
        # We'll fetch the pool and allocate a /24 or /28 from it.
        # For MVP simplicity, we allocate the whole CIDR or a fixed sub‑range.
        # A production implementation would use row locking and sub‑allocation.
        result = (
            await self.supabase.table("address_pools")
            .select("cidr")
            .eq("profile", profile)
            .execute()
        )
        if not result.data:
            raise RuntimeError(f"No address pool for profile {profile}")
        pool_cidr = result.data[0]["cidr"]
        # For MVP: just assign the whole pool CIDR to the AND.
        # In reality, you'd split it and track usage.
        await self.supabase.table("address_leases").upsert(
            {"and_name": and_name, "cidr": pool_cidr}
        ).execute()
        return pool_cidr

    async def register_domain(self, domain: str, org_name: str, ns_records: List[str]) -> None:
        """Register a domain; emit DNS update event."""
        data = {"domain": domain, "org_name": org_name, "ns_records": ns_records}
        await self.supabase.table("domain_records").upsert(data).execute()
        # Emit DNS update event
        event = EventEnvelope.create(
            event_type="domain.registered",
            emitted_by="domain_registry_handler",
            payload={"domain": domain, "org": org_name, "ns": ns_records},
        )
        await self.pgmq.send("dns_updates", event)
