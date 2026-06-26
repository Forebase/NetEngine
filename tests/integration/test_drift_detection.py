"""Integration tests for drift detection and self-healing.

These tests verify drift detection works end-to-end with real orchestrator
and handler instances. They use mock mode to avoid requiring Docker.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.drift_controller import DriftDetectionController
from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.handlers.context import PhaseContext
from netengine.spec.models import NetEngineSpec


class TestDriftDetectionIntegration:
    """Integration tests for drift detection."""

    @pytest.mark.asyncio
    async def test_drift_detection_with_mock_mode(
        self,
        minimal_spec: NetEngineSpec,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Test drift detection in mock mode (no Docker required)."""
        # Setup isolated runtime state
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))

        # Create orchestrator in mock mode
        orchestrator = Orchestrator(minimal_spec, mock_mode=True)

        # Mark some phases as complete
        orchestrator.runtime_state.phase_completed = {
            "0": True,
            "1": True,
            "3": True,
        }

        # Create drift detection controller
        controller = DriftDetectionController(
            orchestrator=orchestrator,
            poll_interval_seconds=1,
            auto_heal=False,
        )

        # Mock healthcheck to return False for one phase
        original_handlers = {}
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 1:
                original_handlers[phase_num] = handler_class
                mock_handler = AsyncMock()
                mock_handler.healthcheck = AsyncMock(return_value=False)
                orchestrator.PHASE_HANDLERS = [
                    (pn, (mock_handler if pn == 1 else h)) for pn, h in orchestrator.PHASE_HANDLERS
                ]

        # Run one iteration
        await controller._run_one_iteration()

        # Verify drift was detected
        assert 1 in controller.drift_states
        assert controller.drift_states[1].is_drifted is True

    @pytest.mark.asyncio
    async def test_drift_detection_multiple_iterations(
        self,
        minimal_spec: NetEngineSpec,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Test drift detection across multiple iterations."""
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))

        orchestrator = Orchestrator(minimal_spec, mock_mode=True)
        orchestrator.runtime_state.phase_completed = {"0": True}

        controller = DriftDetectionController(
            orchestrator=orchestrator,
            poll_interval_seconds=1,
            auto_heal=False,
        )

        # Iteration 1: phase is healthy
        healthy = True
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 0:
                controller._update_drift_state(phase_num, handler_class.__name__, healthy)

        assert controller.drift_states[0].is_drifted is False

        # Iteration 2: phase becomes unhealthy
        healthy = False
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 0:
                controller._update_drift_state(phase_num, handler_class.__name__, healthy)

        assert controller.drift_states[0].is_drifted is True
        assert controller.drift_states[0].consecutive_drift_count == 1

        # Iteration 3: phase still unhealthy
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 0:
                controller._update_drift_state(phase_num, handler_class.__name__, healthy)

        assert controller.drift_states[0].consecutive_drift_count == 2

        # Iteration 4: phase recovers
        healthy = True
        for phase_num, handler_class in orchestrator.PHASE_HANDLERS:
            if phase_num == 0:
                controller._update_drift_state(phase_num, handler_class.__name__, healthy)

        assert controller.drift_states[0].is_drifted is False

    @pytest.mark.asyncio
    async def test_auto_healing_triggers_on_drift(
        self,
        minimal_spec: NetEngineSpec,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Test that auto-healing is triggered when drift is detected."""
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))

        orchestrator = Orchestrator(minimal_spec, mock_mode=True)
        orchestrator.runtime_state.phase_completed = {"0": True}

        # Mock the orchestrator._check_prerequisites to not raise errors
        orchestrator._check_prerequisites = MagicMock()

        controller = DriftDetectionController(
            orchestrator=orchestrator,
            poll_interval_seconds=1,
            auto_heal=True,
        )

        # Create a drifted phase list
        drifted_phases = [0]

        # Mock handler for self-healing
        mock_handler = AsyncMock()
        mock_handler.execute = AsyncMock()
        mock_handler.healthcheck = AsyncMock(return_value=True)

        # Patch orchestrator.PHASE_HANDLERS to return our mock handler
        with patch.object(
            orchestrator,
            'PHASE_HANDLERS',
            [(0, MagicMock(return_value=mock_handler))]
        ):
            # This would trigger self-healing in the real flow
            await controller._trigger_self_healing(drifted_phases)

        # Verify execute was called during healing
        mock_handler.execute.assert_called()

    @pytest.mark.asyncio
    async def test_drift_history_persisted(
        self,
        minimal_spec: NetEngineSpec,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Test that drift history is persisted to RuntimeState."""
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))

        orchestrator = Orchestrator(minimal_spec, mock_mode=True)

        # Add a drift event to history
        orchestrator.runtime_state.drift_history.append({
            "phase_num": 0,
            "detected_at": "2025-06-26T12:00:00",
            "healed_at": "2025-06-26T12:00:05",
            "healing_failed": False,
            "error": None,
        })

        # Save state
        orchestrator.runtime_state.save()

        # Load state in a new instance
        loaded_state = RuntimeState.load()

        # Verify history was persisted
        assert len(loaded_state.drift_history) == 1
        assert loaded_state.drift_history[0]["phase_num"] == 0

    @pytest.mark.asyncio
    async def test_drift_controller_cancellation(
        self,
        minimal_spec: NetEngineSpec,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Test that drift controller gracefully handles cancellation."""
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))

        orchestrator = Orchestrator(minimal_spec, mock_mode=True)

        controller = DriftDetectionController(
            orchestrator=orchestrator,
            poll_interval_seconds=1,
            auto_heal=False,
        )

        # Start the controller in a task and immediately cancel it
        task = asyncio.create_task(controller.run())
        await asyncio.sleep(0.1)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected

    @pytest.mark.asyncio
    async def test_event_emission_on_drift_detection(
        self,
        minimal_spec: NetEngineSpec,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Test that drift events are emitted via pgmq."""
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))

        orchestrator = Orchestrator(minimal_spec, mock_mode=True)
        orchestrator.context.pgmq_client = AsyncMock()
        orchestrator.context.pgmq_client.send = AsyncMock()

        controller = DriftDetectionController(orchestrator=orchestrator)

        # Emit a drift event
        await controller._emit_drift_event(
            phase_num=0,
            handler_name="TestHandler",
            event_type="drift.detected",
            payload={"phase": 0},
        )

        # Verify event was sent
        orchestrator.context.pgmq_client.send.assert_called_once()
        call_args = orchestrator.context.pgmq_client.send.call_args
        event = call_args[0][0] if call_args[0] else call_args[1].get('event')
        assert event is not None
        assert event.event_type == "drift.detected"
