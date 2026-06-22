"""Tests for Orchestrator with M3 phases."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from netengine.core.orchestrator import Orchestrator
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler
from netengine.spec.loader import load_spec


async def _set_substrate_output(context):
    context.runtime_state.substrate_output = {"healthy": True}


async def _set_dns_output(context):
    context.runtime_state.dns_output = {"healthy": True}


async def _set_pki_output(context):
    context.runtime_state.pki_bootstrapped = True


@pytest.fixture
def m3_spec():
    """Load minimal valid spec for orchestrator tests."""
    return load_spec(Path(__file__).parent.parent.parent / "examples" / "minimal.yaml")


class TestOrchestratorPhaseExecution:
    """Tests for Orchestrator.execute_phases() behavior."""

    @pytest.mark.asyncio
    async def test_orchestrator_execute_phases_skips_completed_phase(self, m3_spec):
        """Orchestrator should skip phases already marked complete."""
        orchestrator = Orchestrator(m3_spec)
        orchestrator.runtime_state.phase_completed["0"] = True

        # Mock Phase 0 handler to track if execute was called
        with patch(
            "netengine.core.orchestrator.SubstrateHandler.execute", new_callable=AsyncMock
        ) as mock_execute:
            with patch(
                "netengine.core.orchestrator.SubstrateHandler.should_skip",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch(
                    "netengine.core.orchestrator.DNSHandler.execute", new_callable=AsyncMock
                ):
                    with patch(
                        "netengine.core.orchestrator.DNSHandler.should_skip",
                        new_callable=AsyncMock,
                        return_value=True,
                    ):
                        with patch(
                            "netengine.core.orchestrator.PKIPhaseHandler.execute",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "netengine.core.orchestrator.PKIPhaseHandler.should_skip",
                                new_callable=AsyncMock,
                                return_value=True,
                            ):
                                with patch(
                                    "netengine.core.orchestrator.PKIPhaseHandler.healthcheck",
                                    new_callable=AsyncMock,
                                    return_value=True,
                                ):
                                    await orchestrator.execute_phases(up_to_phase=3)

                                    # Phase 0 should have been skipped (not executed)
                                    mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_orchestrator_execute_phases_up_to_limit(self, m3_spec):
        """Orchestrator should stop execution at up_to_phase limit."""
        orchestrator = Orchestrator(m3_spec)

        with patch(
            "netengine.core.orchestrator.SubstrateHandler.execute",
            new_callable=AsyncMock,
            side_effect=_set_substrate_output,
        ):
            with patch(
                "netengine.core.orchestrator.SubstrateHandler.should_skip",
                new_callable=AsyncMock,
                return_value=False,
            ):
                with patch(
                    "netengine.core.orchestrator.SubstrateHandler.healthcheck",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    with patch(
                        "netengine.core.orchestrator.DNSHandler.execute",
                        new_callable=AsyncMock,
                        side_effect=_set_dns_output,
                    ):
                        with patch(
                            "netengine.core.orchestrator.DNSHandler.should_skip",
                            new_callable=AsyncMock,
                            return_value=False,
                        ):
                            with patch(
                                "netengine.core.orchestrator.DNSHandler.healthcheck",
                                new_callable=AsyncMock,
                                return_value=True,
                            ):
                                with patch.object(
                                    PKIPhaseHandler,
                                    "execute",
                                    new_callable=AsyncMock,
                                    side_effect=_set_pki_output,
                                ):
                                    with patch.object(
                                        PKIPhaseHandler,
                                        "should_skip",
                                        new_callable=AsyncMock,
                                        return_value=False,
                                    ):
                                        with patch.object(
                                            PKIPhaseHandler,
                                            "healthcheck",
                                            new_callable=AsyncMock,
                                            return_value=True,
                                        ):
                                            # Mock remaining phases to not execute
                                            with patch.object(
                                                PlatformIdentityPhaseHandler,
                                                "execute",
                                                new_callable=AsyncMock,
                                            ) as mock_p4_execute:
                                                with patch.object(
                                                    PlatformIdentityPhaseHandler,
                                                    "should_skip",
                                                    new_callable=AsyncMock,
                                                    return_value=False,
                                                ):
                                                    await orchestrator.execute_phases(up_to_phase=3)

                                                    # Phase 4 execute should NOT have been called
                                                    mock_p4_execute.assert_not_called()
                                                    # But phases 0-3 should be complete
                                                    assert orchestrator.runtime_state.phase_completed.get(
                                                        "0", False
                                                    )
                                                    assert orchestrator.runtime_state.phase_completed.get(
                                                        "1", False
                                                    )
                                                    assert orchestrator.runtime_state.phase_completed.get(
                                                        "2", False
                                                    )
                                                    assert orchestrator.runtime_state.phase_completed.get(
                                                        "3", False
                                                    )

    @pytest.mark.asyncio
    async def test_orchestrator_marks_phase_complete_on_success(self, m3_spec):
        """Orchestrator should mark phase as complete after successful execution."""
        orchestrator = Orchestrator(m3_spec)

        with patch(
            "netengine.core.orchestrator.SubstrateHandler.execute",
            new_callable=AsyncMock,
            side_effect=_set_substrate_output,
        ):
            with patch(
                "netengine.core.orchestrator.SubstrateHandler.should_skip",
                new_callable=AsyncMock,
                return_value=False,
            ):
                with patch(
                    "netengine.core.orchestrator.SubstrateHandler.healthcheck",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    with patch(
                        "netengine.core.orchestrator.DNSHandler.execute",
                        new_callable=AsyncMock,
                        side_effect=_set_dns_output,
                    ):
                        with patch(
                            "netengine.core.orchestrator.DNSHandler.should_skip",
                            new_callable=AsyncMock,
                            return_value=False,
                        ):
                            with patch(
                                "netengine.core.orchestrator.DNSHandler.healthcheck",
                                new_callable=AsyncMock,
                                return_value=True,
                            ):
                                await orchestrator.execute_phases(up_to_phase=1)

                                # Phase 0 and 1 should be marked complete
                                assert orchestrator.runtime_state.phase_completed.get("0", False)
                                assert orchestrator.runtime_state.phase_completed.get("1", False)

    @pytest.mark.asyncio
    async def test_orchestrator_raises_on_healthcheck_failure(self, m3_spec):
        """Orchestrator should raise error if phase healthcheck fails."""
        orchestrator = Orchestrator(m3_spec)

        with patch(
            "netengine.core.orchestrator.SubstrateHandler.execute",
            new_callable=AsyncMock,
            side_effect=_set_substrate_output,
        ):
            with patch(
                "netengine.core.orchestrator.SubstrateHandler.should_skip",
                new_callable=AsyncMock,
                return_value=False,
            ):
                with patch(
                    "netengine.core.orchestrator.SubstrateHandler.healthcheck",
                    new_callable=AsyncMock,
                    return_value=False,
                ):
                    with pytest.raises(RuntimeError, match="healthcheck failed"):
                        await orchestrator.execute_phases(up_to_phase=0)

    @pytest.mark.asyncio
    async def test_orchestrator_persists_state_on_error(self, m3_spec):
        """Orchestrator should save error state when phase fails."""
        orchestrator = Orchestrator(m3_spec)

        with patch(
            "netengine.core.orchestrator.SubstrateHandler.execute",
            new_callable=AsyncMock,
            side_effect=RuntimeError("test error"),
        ):
            with patch(
                "netengine.core.orchestrator.SubstrateHandler.should_skip",
                new_callable=AsyncMock,
                return_value=False,
            ):
                try:
                    await orchestrator.execute_phases(up_to_phase=0)
                except RuntimeError:
                    pass

                # Error should be recorded in state
                assert orchestrator.runtime_state.last_error == "test error"


class TestOrchestratorPhaseOrdering:
    """Tests for correct phase ordering and sequencing."""

    def test_phase_ordering_is_sequential(self, m3_spec):
        """Phases should be ordered sequentially 0-8."""
        orchestrator = Orchestrator(m3_spec)

        phases = [phase_num for phase_num, _ in orchestrator.PHASE_HANDLERS]
        assert phases == [0, 1, 3, 4, 5, 6, 7, 8, 9], f"Unexpected phase handler registry: {phases}"

    def test_phase_handlers_are_distinct(self, m3_spec):
        """Each phase should have a handler registered."""
        orchestrator = Orchestrator(m3_spec)

        handlers = {
            phase_num: handler_class for phase_num, handler_class in orchestrator.PHASE_HANDLERS
        }
        assert len(handlers) == 9, f"Expected 9 handler milestones, got {len(handlers)}"
        assert min(handlers.keys()) == 0, "Lowest phase should be 0"
        assert max(handlers.keys()) == 9, "Highest phase should be 9"

    def test_phase_3_before_phase_4(self, m3_spec):
        """Phase 3 should execute before Phase 4."""
        orchestrator = Orchestrator(m3_spec)

        phases = [phase_num for phase_num, _ in orchestrator.PHASE_HANDLERS]
        phase_3_idx = phases.index(3)
        phase_4_idx = phases.index(4)

        assert phase_3_idx < phase_4_idx, "Phase 3 should come before Phase 4"
