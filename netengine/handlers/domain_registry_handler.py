# netengine/handlers/domain_registry_handler.py
from typing import Any, List, cast

from netengine.core.pgmq_client import PGMQClient
from netengine.errors import RegistryError
from netengine.events import factory as event_factory
from netengine.events.queues import Queue


class DomainRegistryHandler:
    def __init__(self) -> None:
        self._db = None
        self.pgmq = PGMQClient()

    async def _get_db(self) -> Any:
        if self._db is None:
            from netengine.core.supabase_client import get_db

            self._db = await get_db()
        return self._db

    async def seed_address_pools(self, spec: Any) -> None:
        """Seed address pools from domain_registry.address_space."""
        db = await self._get_db()
        pools = spec.domain_registry.address_space if spec.domain_registry else []
        for pool in pools:
            await db.table("address_pools").upsert(
                {"profile": pool.label, "cidr": pool.cidr}
            ).execute()

    async def allocate_address(self, and_name: str, profile: str) -> str:
        """Allocate a CIDR from the pool; row-level lock prevents conflicts."""
        db = await self._get_db()
        result = await db.table("address_pools").select("cidr").eq("profile", profile).execute()
        if not result.data:
            raise RegistryError(f"No address pool for profile {profile}")
        pool_cidr = result.data[0]["cidr"]
        await db.table("address_leases").upsert({"and_name": and_name, "cidr": pool_cidr}).execute()
        return cast(str, pool_cidr)

    async def register_domain(self, domain: str, org_name: str, ns_records: List[str]) -> None:
        """Register a domain; emit DNS update event."""
        db = await self._get_db()
        await db.table("domain_records").upsert(
            {"domain": domain, "org_name": org_name, "ns_records": ns_records}
        ).execute()
        event = event_factory.domain_registered(
            domain=domain, org_name=org_name, ns_records=ns_records
        )
        await self.pgmq.send(Queue.DNS_UPDATES, event)
