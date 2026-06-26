"""Integration tests for M3 bootstrap (Phases 3-4: PKI + Platform Identity)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from netengine.core.orchestrator import Orchestrator
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler
from netengine.spec.loader import load_spec

_EXAMPLES = Path(__file__).parent.parent.parent / "examples"


@pytest.fixture
def m3_spec():
    """Full valid spec for M3 orchestrator tests."""
    return load_spec(_EXAMPLES / "minimal.yaml")


class TestPKIPhaseHandlerContract:
    """Tests for Phase 3: PKI + ACME handler contract."""

    @pytest.mark.asyncio
    async def test_phase_3_implements_base_interface(self):
        """Phase 3 should implement BasePhaseHandler interface."""
        handler = PKIPhaseHandler()
        assert hasattr(handler, "execute")
        assert hasattr(handler, "healthcheck")
        assert hasattr(handler, "should_skip")

    @pytest.mark.asyncio
    async def test_phase_3_should_skip_if_completed(self, phase_context):
        """Phase 3 should skip if already completed."""
        handler = PKIPhaseHandler()
        phase_context.runtime_state.phase_completed["3"] = True

        skip = await handler.should_skip(phase_context)
        assert skip is True

    @pytest.mark.asyncio
    async def test_phase_3_should_not_skip_if_not_completed(self, phase_context):
        """Phase 3 should not skip if not yet completed."""
        handler = PKIPhaseHandler()
        phase_context.runtime_state.phase_completed["3"] = False

        skip = await handler.should_skip(phase_context)
        assert skip is False

    @pytest.mark.asyncio
    async def test_phase_3_healthcheck_returns_bool(self, phase_context):
        """Phase 3 healthcheck should always return a boolean."""
        handler = PKIPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)


class TestPlatformIdentityPhaseHandlerContract:
    """Tests for Phase 4: Platform Identity handler contract."""

    @pytest.mark.asyncio
    async def test_phase_4_implements_base_interface(self):
        """Phase 4 should implement BasePhaseHandler interface."""
        handler = PlatformIdentityPhaseHandler()
        assert hasattr(handler, "execute")
        assert hasattr(handler, "healthcheck")
        assert hasattr(handler, "should_skip")

    @pytest.mark.asyncio
    async def test_phase_4_should_skip_if_completed(self, phase_context):
        """Phase 4 should skip if already completed."""
        handler = PlatformIdentityPhaseHandler()
        phase_context.runtime_state.phase_completed["4"] = True

        skip = await handler.should_skip(phase_context)
        assert skip is True

    @pytest.mark.asyncio
    async def test_phase_4_should_not_skip_if_not_completed(self, phase_context):
        """Phase 4 should not skip if not yet completed."""
        handler = PlatformIdentityPhaseHandler()
        phase_context.runtime_state.phase_completed["4"] = False

        skip = await handler.should_skip(phase_context)
        assert skip is False

    @pytest.mark.asyncio
    async def test_phase_4_healthcheck_returns_bool(self, phase_context):
        """Phase 4 healthcheck should always return a boolean."""
        handler = PlatformIdentityPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_phase_4_healthcheck_fails_without_container_id(self, phase_context):
        """Phase 4 healthcheck should fail if container not started."""
        handler = PlatformIdentityPhaseHandler()
        # Ensure no container ID is set
        phase_context.runtime_state.keycloak_platform_container_id = None

        is_healthy = await handler.healthcheck(phase_context)
        assert is_healthy is False


class TestM3OrchestratorIntegration:
    """Tests for M3 bootstrap via Orchestrator."""

    @pytest.mark.asyncio
    async def test_orchestrator_registers_phase_3_and_4_handlers(self, m3_spec):
        """Orchestrator should have Phase 3 and 4 handlers registered."""
        orchestrator = Orchestrator(m3_spec)

        # Check handler registry
        handler_phases = [phase_num for phase_num, _ in orchestrator.PHASE_HANDLERS]
        assert 3 in handler_phases, "Phase 3 (PKI) not registered"
        assert 4 in handler_phases, "Phase 4 (Platform Identity) not registered"

    @pytest.mark.asyncio
    async def test_orchestrator_phase_3_handler_is_pki(self, m3_spec):
        """Orchestrator Phase 3 should use PKIPhaseHandler."""
        orchestrator = Orchestrator(m3_spec)

        phase_3_handler = None
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 3:
                phase_3_handler = handler_class
                break

        assert phase_3_handler == PKIPhaseHandler, "Phase 3 handler is not PKIPhaseHandler"

    @pytest.mark.asyncio
    async def test_orchestrator_phase_4_handler_is_platform_identity(self, m3_spec):
        """Orchestrator Phase 4 should use PlatformIdentityPhaseHandler."""
        orchestrator = Orchestrator(m3_spec)

        phase_4_handler = None
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 4:
                phase_4_handler = handler_class
                break

        assert (
            phase_4_handler == PlatformIdentityPhaseHandler
        ), "Phase 4 handler is not PlatformIdentityPhaseHandler"

    @pytest.mark.asyncio
    async def test_orchestrator_phase_execution_order(self, m3_spec):
        """Orchestrator should execute phases in correct order (0-9)."""
        orchestrator = Orchestrator(m3_spec)

        phase_numbers = [phase_num for phase_num, _ in orchestrator.PHASE_HANDLERS]
        assert phase_numbers == sorted(phase_numbers), "Phases not in ascending order"
        assert phase_numbers == [
            0,
            1,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
        ], "DNS is registered once at Phase 1 and marks Phase 2 complete"
