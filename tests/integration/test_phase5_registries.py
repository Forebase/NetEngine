import asyncio

import pytest

from netengine.core.orchestrator import Orchestrator
from netengine.core.supabase_client import get_supabase
from netengine.spec.loader import load_spec


@pytest.mark.asyncio
async def test_phase_5():
    spec = load_spec("examples/minimal.yaml")
    orch = Orchestrator(spec)
    # Assume phases 0-4 completed
    await orch.phase_5_registries()

    supabase = get_supabase()
    # Check orgs seeded
    orgs = await supabase.table("world_registry").select("*").execute()
    assert len(orgs.data) > 0
    # Check domain registration
    result = (
        await supabase.table("domain_records")
        .insert(
            {
                "domain": "test.internal",
                "org_name": "acme-corp",
                "ns_records": ["ns1.test.internal"],
            }
        )
        .execute()
    )
    # Wait for DNS update to be processed (poll pgmq)
    # ... check that dns_handler.add_zone_record was called (mocked or via integration)
