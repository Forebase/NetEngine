# netengine/handlers/domain_registry_handler.py
import ipaddress
from typing import Any, List, cast

from netengine.core.pgmq_client import PGMQClient
from netengine.errors import RegistryError
from netengine.events import factory as event_factory
from netengine.events.queues import Queue
from netengine.handlers.context import PhaseContext
from netengine.handlers.protocols import PGMQAdapterProtocol


class DomainRegistryHandler:
    def __init__(
        self,
        pgmq: PGMQAdapterProtocol | None = None,
        context: PhaseContext | None = None,
    ) -> None:
        self._db = None
        self.pgmq = pgmq or (context.pgmq_client if context else None) or PGMQClient()

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
        """Allocate a unique AND subnet from the registry phase address pool.

        Existing leases are idempotently reused by ``and_name``. New leases are
        allocated as /24s within the profile pool (or the pool itself when it is
        smaller than /24). The ``address_leases.cidr`` unique constraint is the
        database-backed collision guard; if another allocator wins the same CIDR,
        this method refreshes leases and tries the next free candidate.
        """
        db = await self._get_db()

        existing = (
            await db.table("address_leases")
            .select("and_name,cidr")
            .eq("and_name", and_name)
            .execute()
        )
        if existing.data and existing.data[0].get("and_name") == and_name:
            return cast(str, existing.data[0]["cidr"])

        pool_result = (
            await db.table("address_pools").select("cidr").eq("profile", profile).execute()
        )
        if not pool_result.data:
            raise RegistryError(f"No address pool for profile {profile}")

        pool = ipaddress.ip_network(str(pool_result.data[0]["cidr"]), strict=False)
        prefix = max(pool.prefixlen, 24)
        candidates = [pool] if pool.prefixlen > 24 else list(pool.subnets(new_prefix=prefix))

        last_error: Exception | None = None
        for _ in range(2):
            lease_result = await db.table("address_leases").select("and_name,cidr").execute()
            used = {
                ipaddress.ip_network(str(row["cidr"]), strict=False)
                for row in lease_result.data
                if "and_name" in row
            }
            for candidate in candidates:
                if candidate in used:
                    continue
                cidr = str(candidate)
                try:
                    await db.table("address_leases").upsert(
                        {"and_name": and_name, "cidr": cidr, "profile": profile}
                    ).execute()
                except Exception as exc:
                    last_error = exc
                    if "profile" in str(exc).lower():
                        try:
                            await db.table("address_leases").upsert(
                                {"and_name": and_name, "cidr": cidr}
                            ).execute()
                        except Exception as retry_exc:
                            last_error = retry_exc
                            continue
                    else:
                        continue
                return cidr

        if last_error is not None:
            raise RegistryError(
                f"Address pool exhausted for profile {profile}; last collision: {last_error}"
            )
        raise RegistryError(f"Address pool exhausted for profile {profile}")

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
