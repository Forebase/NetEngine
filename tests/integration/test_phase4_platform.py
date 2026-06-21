import pytest
import asyncio
from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.spec.loader import load_spec


@pytest.mark.asyncio
async def test_phase_4():
    # Load spec, run phases 0-3 (stubbed) and then phase 4
    spec = load_spec("examples/minimal.yaml")
    orch = Orchestrator(spec)
    # Assume phases 0-3 are already run (or call them)
    await orch.phase_3_pki()  # if not run
    # Now run Phase 4 handler directly
    from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler
    handler = PlatformIdentityPhaseHandler()
    await handler.execute(orch.context)

    # Verify:
    state = RuntimeState.load()
    assert state.keycloak_platform_container_id is not None
    assert state.platform_realm_id == "platform"
    # Check DNS: dig auth.platform.internal
    # Check Keycloak reachable: curl -k https://auth.platform.internal/health/ready
    # Check admin user can get token