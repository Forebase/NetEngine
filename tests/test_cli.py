"""CLI command tests."""

import importlib
import json
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
    assert mock_orchestrator.execute_phases.await_count == 1
    assert mock_orchestrator.execute_phases.call_args.kwargs["up_to_phase"] == 9


def test_up_mock_minimal_bootstrap_records_completed_runtime_state(tmp_path: Path):
    """The documented mock smoke path should complete phases without local state writes."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    state_file = tmp_path / "mock-bootstrap-state.json"

    result = CliRunner().invoke(
        cli_main.cli,
        ["up", str(spec_file)],
        env={
            "NETENGINE_MOCK": "true",
            "NETENGINE_STATE_FILE": str(state_file),
        },
    )

    assert result.exit_code == 0, result.output
    assert "World bootstrapped." in result.output
    assert state_file.exists()

    state = json.loads(state_file.read_text())
    assert state["phase_completed"] == {str(phase): True for phase in range(10)}
    assert state["world_spec"]["metadata"]["name"] == "minimal-example"
    assert state["identity_platform_output"]["keycloak_container_id"] == "mock-keycloak-platform"
    assert state["world_registry_output"]["seeded"] is True
    assert state["domain_registry_output"]["address_pools_seeded"] is True
    assert state["identity_inworld_output"]["status"] == "mocked"
    assert state["ands_output"]["status"] == "mocked"
    assert state["world_services_output"]["status"] == "mocked"
    assert state["org_apps_output"]["status"] == "mocked"


def test_up_migration_failure_prevents_orchestrator_startup():
    """Migration failures should stop boot before the orchestrator is created."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with (
        patch.dict("os.environ", {"NETENGINE_DB_URL": "postgresql://example/db"}, clear=False),
        patch("netengine.cli.main._run_migrations", new_callable=AsyncMock) as mock_migrations,
        patch("netengine.cli.main.Orchestrator") as mock_orchestrator_class,
    ):
        mock_migrations.side_effect = RuntimeError("migration boom")

        result = CliRunner().invoke(cli_main.cli, ["up", str(spec_file)])

    assert result.exit_code == 1
    assert "Migrations failed: migration boom" in result.output
    mock_migrations.assert_awaited_once_with("postgresql://example/db")
    mock_orchestrator_class.assert_not_called()


def test_up_allows_migration_failure_with_explicit_flag():
    """The development escape hatch should continue booting after migration failures."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with (
        patch.dict("os.environ", {"NETENGINE_DB_URL": "postgresql://example/db"}, clear=False),
        patch("netengine.cli.main._run_migrations", new_callable=AsyncMock) as mock_migrations,
        patch("netengine.cli.main.Orchestrator") as mock_orchestrator_class,
    ):
        mock_migrations.side_effect = RuntimeError("migration boom")
        mock_orchestrator = mock_orchestrator_class.return_value
        mock_orchestrator.execute_phases = AsyncMock()
        mock_orchestrator.consumer_supervisor.consumers = {}

        result = CliRunner().invoke(
            cli_main.cli, ["up", str(spec_file), "--allow-migration-failure"]
        )

    assert result.exit_code == 0, result.output
    mock_migrations.assert_awaited_once_with("postgresql://example/db")
    mock_orchestrator_class.assert_called_once()
    assert mock_orchestrator.execute_phases.await_count == 1
    assert mock_orchestrator.execute_phases.call_args.kwargs["up_to_phase"] == 9


def test_up_skip_migrations_bypasses_migration_execution():
    """--skip-migrations should remain the intentional migration bypass."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with (
        patch.dict("os.environ", {"NETENGINE_DB_URL": "postgresql://example/db"}, clear=False),
        patch("netengine.cli.main._run_migrations", new_callable=AsyncMock) as mock_migrations,
        patch("netengine.cli.main.Orchestrator") as mock_orchestrator_class,
    ):
        mock_orchestrator = mock_orchestrator_class.return_value
        mock_orchestrator.execute_phases = AsyncMock()
        mock_orchestrator.consumer_supervisor.consumers = {}

        result = CliRunner().invoke(cli_main.cli, ["up", str(spec_file), "--skip-migrations"])

    assert result.exit_code == 0, result.output
    mock_migrations.assert_not_awaited()
    mock_orchestrator_class.assert_called_once()
    assert mock_orchestrator.execute_phases.await_count == 1
    assert mock_orchestrator.execute_phases.call_args.kwargs["up_to_phase"] == 9


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
    assert mock_orchestrator.execute_phases.await_count == 1
    assert mock_orchestrator.execute_phases.call_args.kwargs["up_to_phase"] == 9


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
    from netengine.spec.loader import load_spec

    out_file = tmp_path / "world.yaml"
    CliRunner().invoke(
        cli_main.cli,
        ["init", "--name", "world", "--lifecycle", lifecycle, "--output", str(out_file), "--yes"],
    )
    spec = load_spec(str(out_file))
    assert spec.metadata.lifecycle.value == lifecycle


def test_init_preset_minimal_no_orgs_no_services(tmp_path: Path) -> None:
    """The minimal preset should produce a spec with no orgs and services disabled."""
    from netengine.spec.loader import load_spec

    out_file = tmp_path / "world.yaml"
    result = CliRunner().invoke(
        cli_main.cli,
        ["init", "--preset", "minimal", "--name", "bare", "--output", str(out_file), "--yes"],
    )

    assert result.exit_code == 0, result.output
    spec = load_spec(str(out_file))
    assert spec.world_registry.organizations == []
    assert spec.world_services.mail.enabled is False
    assert spec.world_services.storage.enabled is False
    assert spec.org_apps.catalog == []


def test_init_preset_dev_sandbox_has_two_orgs_and_apps(tmp_path: Path) -> None:
    """The dev-sandbox preset should have two orgs and gitea + mailpit in the catalog."""
    from netengine.spec.loader import load_spec

    out_file = tmp_path / "sandbox.yaml"
    result = CliRunner().invoke(
        cli_main.cli,
        ["init", "--preset", "dev-sandbox", "--name", "sb", "--output", str(out_file), "--yes"],
    )

    assert result.exit_code == 0, result.output
    spec = load_spec(str(out_file))
    assert len(spec.world_registry.organizations) == 2
    assert spec.world_services.mail.enabled is True
    assert spec.world_services.storage.enabled is True
    app_names = [a.name for a in spec.org_apps.catalog]
    assert "gitea" in app_names
    assert "mailpit" in app_names


def test_init_preset_single_org_has_services(tmp_path: Path) -> None:
    """The single-org preset should enable mail, storage, and gitea."""
    from netengine.spec.loader import load_spec

    out_file = tmp_path / "world.yaml"
    result = CliRunner().invoke(
        cli_main.cli,
        ["init", "--preset", "single-org", "--name", "myco", "--output", str(out_file), "--yes"],
    )

    assert result.exit_code == 0, result.output
    spec = load_spec(str(out_file))
    assert spec.world_services.mail.enabled is True
    assert spec.world_services.storage.enabled is True
    assert any(a.name == "gitea" for a in spec.org_apps.catalog)


def test_init_custom_subnet_appears_in_spec(tmp_path: Path) -> None:
    """Custom subnets passed through --yes defaults should land in the generated spec."""
    from netengine.cli.init_wizard import WorldConfig, build_spec_dict

    cfg = WorldConfig(name="test", platform_subnet="10.200.0.0/24", core_subnet="10.201.0.0/24")
    spec = build_spec_dict(cfg)
    assert spec["substrate"]["networks"]["platform"]["subnet"] == "10.200.0.0/24"  # type: ignore[index]
    assert spec["substrate"]["networks"]["core"]["subnet"] == "10.201.0.0/24"  # type: ignore[index]


def test_init_orgs_generate_and_instances(tmp_path: Path) -> None:
    """Orgs added in WorldConfig should produce matching AND instances and in-world users."""
    from netengine.cli.init_wizard import OrgConfig, WorldConfig, build_spec_dict

    cfg = WorldConfig(
        name="test",
        orgs=[
            OrgConfig(
                name="acme",
                and_profile="business",
                users=[{"username": "alice", "email": "alice@acme.internal"}],
            )
        ],
    )
    spec = build_spec_dict(cfg)
    ands = spec["ands"]  # type: ignore[index]
    assert "business" in ands["profiles"]  # type: ignore[index]
    instances = ands["instances"]  # type: ignore[index]
    assert any(i["org"] == "acme" for i in instances)  # type: ignore[index]
    org_users = spec["identity_inworld"]["org_users"]  # type: ignore[index]
    assert any(ou["org"] == "acme" for ou in org_users)  # type: ignore[index]


def test_init_ip_allocation_follows_core_subnet(tmp_path: Path) -> None:
    """Service IPs should be computed from the configured core subnet."""
    from netengine.cli.init_wizard import WorldConfig, build_spec_dict

    cfg = WorldConfig(name="test", core_subnet="192.168.100.0/24")
    spec = build_spec_dict(cfg)
    assert spec["dns"]["root"]["listen_ip"] == "192.168.100.2"  # type: ignore[index]
    assert spec["pki"]["acme"]["listen_ip"] == "192.168.100.6"  # type: ignore[index]
    assert spec["identity_platform"]["listen_ip"] == "192.168.100.7"  # type: ignore[index]


def test_init_summary_shows_org_count(tmp_path: Path) -> None:
    """The dev-sandbox preset summary should mention both orgs."""
    out_file = tmp_path / "sb.yaml"
    result = CliRunner().invoke(
        cli_main.cli,
        ["init", "--preset", "dev-sandbox", "--name", "sb", "--output", str(out_file), "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "acme-corp" in result.output
    assert "bob-home" in result.output
    assert "mail" in result.output


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
    assert mock_orchestrator.execute_phases.await_count == 1
    assert mock_orchestrator.execute_phases.call_args.kwargs["up_to_phase"] == 9


def _write_cli_validate_spec(tmp_path: Path, **updates) -> Path:
    """Write an example-derived spec for validate command tests."""
    import copy

    import yaml

    source = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    data = yaml.safe_load(source.read_text())
    data = copy.deepcopy(data)

    for dotted_path, value in updates.items():
        cursor = data
        parts = dotted_path.split("__")
        for part in parts[:-1]:
            cursor = cursor[part]
        cursor[parts[-1]] = value

    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(data, sort_keys=False))
    return spec_file


def test_validate_valid_spec_exits_zero() -> None:
    """The validate command should accept a valid spec without booting."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    result = CliRunner().invoke(cli_main.cli, ["validate", str(spec_file)])

    assert result.exit_code == 0, result.output
    assert "Spec validation succeeded: minimal-example" in result.output


def test_validate_experimental_gateway_mode_exits_zero_with_explanation(tmp_path: Path) -> None:
    """Mirrored gateway mode is wired but remains experimental during alpha."""
    spec_file = _write_cli_validate_spec(
        tmp_path,
        gateway_portal__real_internet__mode="mirrored",
        gateway_portal__real_internet__service_mirrors=[
            {"real_hostname": "github.com", "in_world_service": "10.0.1.20"}
        ],
    )

    result = CliRunner().invoke(cli_main.cli, ["validate", str(spec_file), "--explain"])

    assert result.exit_code == 0, result.output
    assert "Spec validation succeeded" in result.output
    assert "WARNING:" in result.output
    assert "gateway_portal.real_internet.mode: experimental" in result.output


def test_validate_experimental_enabled_feature_exits_zero_with_explanation(tmp_path: Path) -> None:
    """Experimental active features should validate but be visible to operators."""
    spec_file = _write_cli_validate_spec(tmp_path, pki__intermediate_ca_enabled=True)

    result = CliRunner().invoke(cli_main.cli, ["validate", str(spec_file), "--explain"])

    assert result.exit_code == 0, result.output
    assert "Spec validation succeeded" in result.output
    assert "WARNING:" in result.output
    assert "pki.intermediate_ca_enabled: experimental" in result.output


def test_validate_json_reports_active_feature_states(tmp_path: Path) -> None:
    """JSON format should expose machine-readable support-matrix results."""
    spec_file = _write_cli_validate_spec(tmp_path, pki__intermediate_ca_enabled=True)

    result = CliRunner().invoke(cli_main.cli, ["validate", str(spec_file), "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["spec"] == "minimal-example"
    assert payload["feature_states"] == [
        {
            "path": "pki.intermediate_ca_enabled",
            "state": "experimental",
            "stage": "alpha",
            "reason": "intermediate CA handling is available but still stabilizing",
            "current_value": True,
            "default_value": False,
        }
    ]


def test_validate_json_unsupported_active_feature_exits_nonzero(tmp_path: Path) -> None:
    """Unsupported active fields should be machine-readable and fail CI."""
    spec_file = _write_cli_validate_spec(
        tmp_path, gateway_portal__real_internet__upstream_resolver_enabled=True
    )

    result = CliRunner().invoke(cli_main.cli, ["validate", str(spec_file), "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert (
        payload["feature_states"][0]["path"]
        == "gateway_portal.real_internet.upstream_resolver_enabled"
    )
    assert payload["feature_states"][0]["state"] == "unsupported"
    assert payload["feature_states"][0]["current_value"] is True
    assert payload["feature_states"][0]["default_value"] is False


def test_validate_explain_includes_dotted_field_paths_and_feature_states(tmp_path: Path) -> None:
    """--explain should show concrete dotted paths and their feature states."""
    spec_file = _write_cli_validate_spec(tmp_path, pki__intermediate_ca_enabled=True)

    result = CliRunner().invoke(cli_main.cli, ["validate", str(spec_file), "--explain"])

    assert result.exit_code == 0, result.output
    assert "Feature-state details:" in result.output
    assert "pki.intermediate_ca_enabled" in result.output
    assert "experimental" in result.output
    assert "alpha" in result.output


def test_setup_local_orchestrates_doctor_compose_migrations_and_bootstrap(tmp_path: Path):
    """Setup should coordinate preflight, compose, migrations, readiness, then up."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    ok = cli_main.DoctorCheckResult("ok", cli_main.DoctorStatus.OK, "ready", group="test")

    with (
        patch("netengine.cli.main._run_setup_host_checks", return_value=[ok]) as host_checks,
        patch("netengine.cli.main._docker_compose_up") as compose_up,
        patch("netengine.cli.main._wait_for_postgres_health") as wait_postgres,
        patch("netengine.cli.main._run_migrations", new_callable=AsyncMock) as migrations,
        patch("netengine.cli.main.run_preflight", return_value=[ok]) as readiness,
        patch(
            "netengine.cli.main._check_migration_readiness",
            new_callable=AsyncMock,
            return_value=[ok],
        ),
        patch("netengine.cli.main._feature_state_readiness_results", return_value=[ok]),
        patch("netengine.cli.main._up", new_callable=AsyncMock) as up,
    ):
        result = CliRunner().invoke(
            cli_main.cli,
            [
                "setup",
                "local",
                str(spec_file),
                "--db-url",
                "postgresql://example/db",
                "--state-file",
                str(tmp_path / "state.json"),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Step 1/5: host checks before Postgres starts" in result.output
    assert "Setup checks passed" in result.output
    host_checks.assert_called_once()
    compose_up.assert_called_once_with(("postgres",))
    wait_postgres.assert_called_once_with(timeout_seconds=120.0)
    migrations.assert_awaited_once_with("postgresql://example/db")
    readiness.assert_called_once()
    up.assert_awaited_once()
    assert up.call_args.kwargs["skip_migrations"] is True


def test_setup_local_stops_before_compose_when_required_host_check_fails(tmp_path: Path):
    """Setup should not start infrastructure when required pre-Postgres checks fail."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    failed = cli_main.DoctorCheckResult(
        "Docker container names",
        cli_main.DoctorStatus.FAIL,
        "netengine_postgres",
        "Stop/remove conflicting containers before startup.",
        "docker",
        required=True,
    )

    with (
        patch("netengine.cli.main._run_setup_host_checks", return_value=[failed]),
        patch("netengine.cli.main._docker_compose_up") as compose_up,
        patch("netengine.cli.main._up", new_callable=AsyncMock) as up,
    ):
        result = CliRunner().invoke(
            cli_main.cli,
            ["setup", "local", str(spec_file), "--state-file", str(tmp_path / "state.json")],
        )

    assert result.exit_code == 1
    assert "Hint: Stop/remove conflicting containers before startup." in result.output
    assert "required host checks failed" in result.output
    compose_up.assert_not_called()
    up.assert_not_awaited()


def test_setup_local_stops_before_up_when_spec_aware_check_fails(tmp_path: Path):
    """Setup should stop after migrations when spec-aware doctor checks fail."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    ok = cli_main.DoctorCheckResult("ok", cli_main.DoctorStatus.OK, "ready", group="test")
    failed = cli_main.DoctorCheckResult(
        "Database connectivity",
        cli_main.DoctorStatus.FAIL,
        "connection refused",
        "Verify database connectivity, credentials, and pgmq/Postgres availability.",
        "database",
        required=True,
    )

    with (
        patch("netengine.cli.main._run_setup_host_checks", return_value=[ok]),
        patch("netengine.cli.main._docker_compose_up"),
        patch("netengine.cli.main._wait_for_postgres_health"),
        patch("netengine.cli.main._run_migrations", new_callable=AsyncMock),
        patch("netengine.cli.main.run_preflight", return_value=[failed]),
        patch(
            "netengine.cli.main._check_migration_readiness", new_callable=AsyncMock, return_value=[]
        ),
        patch("netengine.cli.main._feature_state_readiness_results", return_value=[]),
        patch("netengine.cli.main._up", new_callable=AsyncMock) as up,
    ):
        result = CliRunner().invoke(
            cli_main.cli,
            ["setup", "local", str(spec_file), "--db-url", "postgresql://example/db"],
        )

    assert result.exit_code == 1
    assert "required setup checks failed; NetEngine stopped before `netengine up`" in result.output
    assert "Verify database connectivity" in result.output
    up.assert_not_awaited()
