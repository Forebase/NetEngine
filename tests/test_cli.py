"""CLI command tests."""

import importlib
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import click
from click.testing import CliRunner

from netengine.cli import main as cli_main
from netengine.core.state import RuntimeState


def test_cli_imports_orchestrator_from_netengine_package():
    """The CLI should import the orchestrator from the netengine package path."""
    assert cli_main.Orchestrator.__module__ == "netengine.core.orchestrator"


def test_poetry_console_script_points_to_click_group():
    """The documented console script should expose the Click CLI group."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    script_target = pyproject["tool"]["poetry"]["scripts"]["netengine"]
    module_path, object_name = script_target.split(":")
    script_object = getattr(importlib.import_module(module_path), object_name)

    assert script_target == "netengine.cli.main:cli"
    assert script_object is cli_main.cli
    assert isinstance(script_object, click.Group)
    assert script_object.name == "cli"


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
