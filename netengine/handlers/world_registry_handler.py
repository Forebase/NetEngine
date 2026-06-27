from typing import Any, List

from netengine.core.pgmq_client import PGMQClient
from netengine.events.schema import EventEnvelope


class WorldRegistryHandler:
    def __init__(self):
        self._db = None
        self.pgmq = PGMQClient()

    async def _get_db(self):
        if self._db is None:
            from netengine.core.supabase_client import get_db

            self._db = await get_db()
        return self._db

    async def seed_from_spec(self, spec: Any) -> None:
        """Idempotent seed: create orgs from world_registry.organizations."""
        orgs = spec.world_registry.organizations if spec.world_registry else []
        for org in orgs:
            await self.admit_org(
                name=org.name,
                capabilities=[c.value for c in org.capabilities],
                and_profile=org.and_profile.value,
            )

    async def admit_org(self, name: str, capabilities: List[str], and_profile: str) -> None:
        """Admit a new org; idempotent (upsert)."""
        db = await self._get_db()
        data = {"org_name": name, "capabilities": capabilities, "and_profile": and_profile}
        await db.table("world_registry").upsert(data).execute()
        event = EventEnvelope.create(
            event_type="org.admitted",
            emitted_by="world_registry_handler",
            payload={"org_name": name, "capabilities": capabilities, "and_profile": and_profile},
        )
        await self.pgmq.send("oidc_provisioning", event)
        await self.pgmq.send("and_provisioning", event)

    async def list_orgs(self) -> List[Any]:
        """Return all orgs in the world registry."""
        db = await self._get_db()
        result = await db.table("world_registry").select("*").execute()
        return result.data or []

    async def get_org(self, name: str) -> Any:
        """Return a single org by name, or None if not found."""
        db = await self._get_db()
        result = await db.table("world_registry").select("*").eq("org_name", name).execute()
        return result.data[0] if result.data else None

    async def update_org(self, name: str, capabilities: List[str], and_profile: str) -> None:
        """Update an org's capabilities and AND profile."""
        db = await self._get_db()
        await db.table("world_registry").update(
            {"capabilities": capabilities, "and_profile": and_profile}
        ).eq("org_name", name).execute()
        event = EventEnvelope.create(
            event_type="org.updated",
            emitted_by="world_registry_handler",
            payload={"org_name": name, "capabilities": capabilities, "and_profile": and_profile},
        )
        await self.pgmq.send("oidc_provisioning", event)
        await self.pgmq.send("and_provisioning", event)

    async def remove_org(self, name: str) -> bool:
        """Remove an org from the world registry. Returns True if it existed."""
        db = await self._get_db()
        existing = await self.get_org(name)
        if not existing:
            return False
        await db.table("world_registry").delete().eq("org_name", name).execute()
        event = EventEnvelope.create(
            event_type="org.removed",
            emitted_by="world_registry_handler",
            payload={"org_name": name},
        )
        await self.pgmq.send("oidc_provisioning", event)
        await self.pgmq.send("and_provisioning", event)
        return True
