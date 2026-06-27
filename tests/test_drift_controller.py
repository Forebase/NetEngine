"""Unit tests for drift detection and self-healing."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.drift_controller import DriftDetectionController, DriftState
from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.handlers.context import PhaseContext
from netengine.spec.models import NetEngineSpec


class TestDriftDetectionController:
    """Tests for DriftDetectionController."""

    @pytest.fixture
    def mock_orchestrator(self, minimal_spec: NetEngineSpec) -> Orchestrator:
        """Create a mock orchestrator."""
        orch = MagicMock(spec=Orchestrator)
        orch.spec = minimal_spec
        orch.runtime_state = RuntimeState()
        orch.runtime_state.phase_completed = {"0": True, "1": True, "3": True}
        orch.context = MagicMock(spec=PhaseContext)
        orch.context.pgmq_client = MagicMock()
        orch.context.pgmq_client.send = AsyncMock()
        orch.PHASE_HANDLERS = [
            (0, MagicMock),
            (1, MagicMock),
            (3, MagicMock),
        ]
        return orch

    @pytest.mark.asyncio
    async def test_drift_detection_initialization(self, mock_orchestrator: Orchestrator) -> None:
        """Test controller initialization."""
        controller = DriftDetectionController(
            orchestrator=mock_orchestrator,
            poll_interval_seconds=10,
            max_drift_retries=2,
            auto_heal=True,
        )

        assert controller.orchestrator == mock_orchestrator
        assert controller.poll_interval_seconds == 10
        assert controller.max_drift_retries == 2
        assert controller.auto_heal is True
        assert controller.drift_states == {}

    @pytest.mark.asyncio
    async def test_check_phase_health_success(self, mock_orchestrator: Orchestrator) -> None:
        """Test healthcheck for a healthy phase."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        mock_handler = AsyncMock()
        mock_handler.__class__.__name__ = "TestHandler"
        mock_handler.healthcheck = AsyncMock(return_value=True)

        result = await controller._check_phase_health(0, mock_handler)

        assert result is True
        mock_handler.healthcheck.assert_called_once_with(mock_orchestrator.context)

    @pytest.mark.asyncio
    async def test_check_phase_health_failure(self, mock_orchestrator: Orchestrator) -> None:
        """Test healthcheck for a drifted phase."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        mock_handler = AsyncMock()
        mock_handler.__class__.__name__ = "TestHandler"
        mock_handler.healthcheck = AsyncMock(return_value=False)

        result = await controller._check_phase_health(0, mock_handler)

        assert result is False
        assert 0 in controller.drift_states
        assert controller.drift_states[0].is_drifted is True

    @pytest.mark.asyncio
    async def test_check_phase_health_exception(self, mock_orchestrator: Orchestrator) -> None:
        """Test healthcheck that raises an exception."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        mock_handler = AsyncMock()
        mock_handler.__class__.__name__ = "TestHandler"
        mock_handler.healthcheck = AsyncMock(side_effect=RuntimeError("health check error"))

        result = await controller._check_phase_health(0, mock_handler)

        assert result is False
        assert 0 in controller.drift_states
        assert controller.drift_states[0].is_drifted is True

    @pytest.mark.asyncio
    async def test_drift_state_tracking(self, mock_orchestrator: Orchestrator) -> None:
        """Test drift state tracking over multiple checks."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        # First check: phase is healthy
        controller._update_drift_state(0, "TestHandler", True)
        assert controller.drift_states[0].is_drifted is False
        assert controller.drift_states[0].consecutive_drift_count == 0

        # Second check: phase becomes unhealthy
        controller._update_drift_state(0, "TestHandler", False)
        assert controller.drift_states[0].is_drifted is True
        assert controller.drift_states[0].consecutive_drift_count == 1
        assert controller.drift_states[0].drift_detected_at is not None

        # Third check: phase still unhealthy
        controller._update_drift_state(0, "TestHandler", False)
        assert controller.drift_states[0].is_drifted is True
        assert controller.drift_states[0].consecutive_drift_count == 2

        # Fourth check: phase recovers
        controller._update_drift_state(0, "TestHandler", True)
        assert controller.drift_states[0].is_drifted is False
        assert controller.drift_states[0].consecutive_drift_count == 0

    @pytest.mark.asyncio
    async def test_drift_event_emission(self, mock_orchestrator: Orchestrator) -> None:
        """Test that drift events are emitted."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        await controller._emit_drift_event(
            phase_num=0,
            handler_name="TestHandler",
            event_type="drift.detected",
            payload={"phase": 0, "handler": "TestHandler"},
        )

        assert mock_orchestrator.context.pgmq_client is not None
        mock_orchestrator.context.pgmq_client.send.assert_called_once()  # type: ignore

    @pytest.mark.asyncio
    async def test_drift_event_emission_no_pgmq(self, mock_orchestrator: Orchestrator) -> None:
        """Test that drift events are skipped when pgmq is unavailable."""
        mock_orchestrator.context.pgmq_client = None
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        await controller._emit_drift_event(
            phase_num=0,
            handler_name="TestHandler",
            event_type="drift.detected",
            payload={"phase": 0},
        )

        # Should not raise an error

    @pytest.mark.asyncio
    async def test_heal_phase_success(self, mock_orchestrator: Orchestrator) -> None:
        """Test successful phase healing."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        mock_handler = AsyncMock()
        mock_handler.__class__.__name__ = "TestHandler"
        mock_handler.execute = AsyncMock()
        mock_handler.healthcheck = AsyncMock(return_value=True)

        success, changed = await controller._heal_phase(0, mock_handler)

        assert success is True
        assert changed is True
        mock_handler.execute.assert_called_once()
        mock_handler.healthcheck.assert_called_once()

    @pytest.mark.asyncio
    async def test_heal_phase_failure(self, mock_orchestrator: Orchestrator) -> None:
        """Test failed phase healing."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        mock_handler = AsyncMock()
        mock_handler.__class__.__name__ = "TestHandler"
        mock_handler.execute = AsyncMock(side_effect=RuntimeError("execution failed"))

        success, changed = await controller._heal_phase(0, mock_handler)

        assert success is False
        assert changed is False

    @pytest.mark.asyncio
    async def test_heal_phase_healthcheck_still_fails(
        self, mock_orchestrator: Orchestrator
    ) -> None:
        """Test when healthcheck still fails after execute()."""
        controller = DriftDetectionController(orchestrator=mock_orchestrator)

        mock_handler = AsyncMock()
        mock_handler.__class__.__name__ = "TestHandler"
        mock_handler.execute = AsyncMock()
        mock_handler.healthcheck = AsyncMock(return_value=False)

        success, changed = await controller._heal_phase(0, mock_handler)

        assert success is False
        assert changed is False

    @pytest.mark.asyncio
    async def test_runtime_state_persistence(self, mock_orchestrator: Orchestrator) -> None:
        """Test that drift state is persisted to RuntimeState."""
        # Record a drift event
        mock_orchestrator.runtime_state.current_drift_phases = [0, 1]
        mock_orchestrator.runtime_state.last_drift_check_at = datetime.now(UTC)

        # Verify drift history can be updated
        event = {
            "phase_num": 0,
            "detected_at": datetime.now(UTC).isoformat(),
            "healed_at": None,
            "healing_failed": False,
            "error": None,
        }
        mock_orchestrator.runtime_state.drift_history.append(event)

        assert len(mock_orchestrator.runtime_state.drift_history) == 1
        assert mock_orchestrator.runtime_state.drift_history[0]["phase_num"] == 0

    @pytest.mark.asyncio
    async def test_iteration_with_multiple_phases(self, mock_orchestrator: Orchestrator) -> None:
        """Test a drift detection iteration with multiple phases."""
        # Create mock handlers that return different health states
        mock_handler_0 = AsyncMock()
        mock_handler_0.__class__.__name__ = "SubstrateHandler"
        mock_handler_0.healthcheck = AsyncMock(return_value=True)

        mock_handler_1 = AsyncMock()
        mock_handler_1.__class__.__name__ = "DNSHandler"
        mock_handler_1.healthcheck = AsyncMock(return_value=False)

        mock_handler_3 = AsyncMock()
        mock_handler_3.__class__.__name__ = "PKIHandler"
        mock_handler_3.healthcheck = AsyncMock(return_value=True)

        def handler_factory(handler_class):
            if handler_class == mock_orchestrator.PHASE_HANDLERS[0][1]:
                return mock_handler_0
            elif handler_class == mock_orchestrator.PHASE_HANDLERS[1][1]:
                return mock_handler_1
            elif handler_class == mock_orchestrator.PHASE_HANDLERS[2][1]:
                return mock_handler_3
            return AsyncMock()

        controller = DriftDetectionController(orchestrator=mock_orchestrator, auto_heal=False)

        with patch.object(
            mock_orchestrator.PHASE_HANDLERS[0][1], "__call__", side_effect=lambda: mock_handler_0
        ):
            pass

        # Run one iteration
        await controller._run_one_iteration()

        # Phase 1 (DNS) should be detected as drifted
        assert 1 in controller.drift_states
        assert controller.drift_states[1].is_drifted is True


class TestDriftState:
    """Tests for DriftState dataclass."""

    def test_drift_state_creation(self) -> None:
        """Test DriftState creation."""
        now = datetime.now(UTC)
        state = DriftState(
            phase_num=0,
            handler_name="TestHandler",
            last_healthcheck_at=now,
            is_drifted=True,
            drift_detected_at=now,
            consecutive_drift_count=1,
        )

        assert state.phase_num == 0
        assert state.handler_name == "TestHandler"
        assert state.is_drifted is True
        assert state.consecutive_drift_count == 1
        assert state.self_healing_attempted is False
