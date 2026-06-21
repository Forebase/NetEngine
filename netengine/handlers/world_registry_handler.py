from typing import Dict, Any, List
from netengine.core.supabase_client import get_supabase
from netengine.events.schema import EventEnvelope
from netengine.core.pgmq_client import PGMQClient

class WorldRegistryHandler:
    def __init__(self):
        self.supabase = get_supabase()
        self.pgmq = PGMQClient()

    async def seed_from_spec(self, spec: Dict[str, Any]) -> None:
        """Idempotent seed: create orgs from world_registry.organizations."""
        orgs = spec.get("world_registry", {}).get("organizations", [])
        for org in orgs:
            await self.admit_org(
                name=org["name"],
                capabilities=org.get("capabilities", []),
                and_profile=org.get("and_profile", "business")
            )

    async def admit_org(self, name: str, capabilities: List[str], and_profile: str) -> None:
        """Admit a new org; idempotent (upsert)."""
        # Upsert into world_registry
        data = {
            "org_name": name,
            "capabilities": capabilities,
            "and_profile": and_profile
        }
        await self.supabase.table("world_registry").upsert(data).execute()
        # Emit event for downstream (M5: OIDC realm, M6: AND provisioning)
        event = EventEnvelope.create(
            event_type="org.admitted",
            emitted_by="world_registry_handler",
            payload={"org_name": name, "capabilities": capabilities, "and_profile": and_profile}
        )
        await self.pgmq.send("oidc_provisioning", event)  # triggers M5
        await self.pgmq.send("and_provisioning", event)   # triggers M6