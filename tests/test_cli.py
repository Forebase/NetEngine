"""CLI command tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from netengine.cli import main as cli_main


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
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=8)


def test_up_supports_environment_loader_option():
    """The up command should load environment overlays when --env is provided."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with (
        patch(
            "netengine.cli.main.load_spec_with_environment",
            wraps=cli_main.load_spec_with_environment,
        ) as mock_loader,
        patch("netengine.cli.main.Orchestrator") as mock_orchestrator_class,
    ):
        mock_orchestrator = mock_orchestrator_class.return_value
        mock_orchestrator.execute_phases = AsyncMock()
        mock_orchestrator.consumer_supervisor.consumers = {}

        result = CliRunner().invoke(cli_main.cli, ["up", str(spec_file), "--env", "dev"])

    assert result.exit_code == 0, result.output
    mock_loader.assert_called_once_with(str(spec_file), environment="dev", overrides=None)
    spec_arg = mock_orchestrator_class.call_args.args[0]
    assert spec_arg.metadata.name == "minimal-example"
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=8)


def test_up_supports_repeatable_set_overrides():
    """The up command should pass repeatable dotted --set overrides into composition loading."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with (
        patch(
            "netengine.cli.main.load_spec_with_composition",
            wraps=cli_main.load_spec_with_composition,
        ) as mock_loader,
        patch("netengine.cli.main.Orchestrator") as mock_orchestrator_class,
    ):
        mock_orchestrator = mock_orchestrator_class.return_value
        mock_orchestrator.execute_phases = AsyncMock()
        mock_orchestrator.consumer_supervisor.consumers = {}

        result = CliRunner().invoke(
            cli_main.cli,
            [
                "up",
                str(spec_file),
                "--set",
                "metadata.name=my-world",
                "--set",
                "world_services.mail.enabled=true",
            ],
        )

    assert result.exit_code == 0, result.output
    mock_loader.assert_called_once_with(
        str(spec_file),
        overrides={"metadata": {"name": "my-world"}, "world_services": {"mail": {"enabled": True}}},
    )
    spec_arg = mock_orchestrator_class.call_args.args[0]
    assert spec_arg.metadata.name == "my-world"
    assert spec_arg.world_services.mail.enabled is True
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=8)
