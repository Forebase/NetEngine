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
