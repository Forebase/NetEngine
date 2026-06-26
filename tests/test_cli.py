"""CLI command tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from netengine.cli import main as cli_main
from netengine.core.state import RuntimeState


def test_cli_imports_orchestrator_from_netengine_package():
    """The CLI should import the orchestrator from the netengine package path."""
    assert cli_main.Orchestrator.__module__ == "netengine.core.orchestrator"


def test_up_invokes_execute_phases_with_example_spec():
    """The up command should load an example spec and execute orchestrator phases."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with patch("netengine.cli.main.Orchestrator") as mock_orchestrator_class:
        mock_orchestrator = mock_orchestrator_class.return_value
        mock_orchestrator.execute_phases = AsyncMock()
        # Mock consumer_supervisor as empty (no consumers registered)
        mock_orchestrator.consumer_supervisor.consumers = {}

        result = CliRunner().invoke(cli_main.cli, ["up", str(spec_file)])

    assert result.exit_code == 0, result.output
    mock_orchestrator_class.assert_called_once()
    spec_arg = mock_orchestrator_class.call_args.args[0]
    assert spec_arg.metadata.name == "minimal-example"
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=9)


def test_status_output_includes_phase_9():
    """The status command should show Phase 9 org applications."""
    state = RuntimeState(phase_completed={"9": True})

    with patch("netengine.cli.main.RuntimeState.load", return_value=state):
        result = CliRunner().invoke(cli_main.cli, ["status"])

    assert result.exit_code == 0, result.output
    assert "✓  Phase 9: Org applications" in result.output
