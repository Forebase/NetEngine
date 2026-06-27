"""CLI command tests."""

import importlib
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import click
import pytest
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
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=9)


def test_init_creates_spec_file(tmp_path: Path) -> None:
    """The init command should write a valid parseable spec and print next steps."""
    from netengine.spec.loader import load_spec

    out_file = tmp_path / "hello.yaml"
    result = CliRunner().invoke(
        cli_main.cli,
        ["init", "--name", "hello", "--lifecycle", "ephemeral", "--output", str(out_file), "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert out_file.exists()
    spec = load_spec(str(out_file))
    assert spec.metadata.name == "hello"
    assert "netengine up" in result.output
    assert "netengine status" in result.output


def test_init_uses_name_as_default_output_path(tmp_path: Path) -> None:
    """Without --output the init command writes to <name>.yaml in cwd."""
    import os

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = CliRunner().invoke(
            cli_main.cli,
            ["init", "--name", "my-world", "--lifecycle", "ephemeral", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "my-world.yaml").exists()
    finally:
        os.chdir(original_cwd)


def test_init_aborts_on_existing_file_without_yes(tmp_path: Path) -> None:
    """Without --yes the init command should prompt before overwriting an existing file."""
    out_file = tmp_path / "world.yaml"
    out_file.write_text("original")

    result = CliRunner().invoke(
        cli_main.cli,
        ["init", "--name", "world", "--lifecycle", "ephemeral", "--output", str(out_file)],
        input="n\n",
    )

    assert result.exit_code != 0
    assert out_file.read_text() == "original"


@pytest.mark.parametrize("lifecycle", ["ephemeral", "persistent"])
def test_init_lifecycle_propagates_to_spec(tmp_path: Path, lifecycle: str) -> None:
    """The lifecycle flag should appear verbatim in the written spec."""
    out_file = tmp_path / "world.yaml"
    CliRunner().invoke(
        cli_main.cli,
        ["init", "--name", "world", "--lifecycle", lifecycle, "--output", str(out_file), "--yes"],
    )
    content = out_file.read_text()
    assert f"lifecycle: {lifecycle}" in content


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
    mock_orchestrator.execute_phases.assert_awaited_once_with(up_to_phase=9)
