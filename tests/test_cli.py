"""CLI command tests."""

import importlib
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import click
from click.testing import CliRunner

from netengine.cli import main as cli_main


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
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=8)
