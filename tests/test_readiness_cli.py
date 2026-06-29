"""Readiness CLI tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from netengine.cli import main as cli_main
from netengine.core.migrations import MigrationStatus
from netengine.diagnostic.preflight import DoctorCheckResult, DoctorStatus
from tests.test_cli import _write_cli_validate_spec


def _healthy_migration_status() -> MigrationStatus:
    return MigrationStatus(
        applied=[],
        pending=[],
        failed=[],
        checksum_drifted=[],
        pgmq_available=True,
        pgmq_installed=True,
        missing_queues=[],
    )


def test_readiness_loads_spec_runs_host_and_migration_checks() -> None:
    """Readiness should validate the spec, run preflight probes, and inspect migrations."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    host_results = [
        DoctorCheckResult("Docker daemon", DoctorStatus.OK, "docker info succeeded", group="docker")
    ]

    with (
        patch("netengine.cli.main.run_preflight", return_value=host_results) as mock_preflight,
        patch("netengine.cli.main.MigrationService") as mock_service_class,
    ):
        mock_service = mock_service_class.return_value
        mock_service.status = AsyncMock(return_value=_healthy_migration_status())

        result = CliRunner().invoke(
            cli_main.cli,
            ["readiness", str(spec_file), "--db-url", "postgresql://example/db"],
        )

    assert result.exit_code == 0, result.output
    assert "NetEngine readiness summary for minimal-example" in result.output
    assert "[PASS] spec: validated minimal-example" in result.output
    assert "[PASS] Docker daemon: docker info succeeded" in result.output
    assert "[PASS] migrations:pending: 0 pending migration(s)" in result.output
    mock_preflight.assert_called_once()
    mock_service_class.assert_called_once_with("postgresql://example/db", cli_main.MIGRATIONS_DIR)
    mock_service.status.assert_awaited_once()


def test_readiness_fails_when_migrations_are_pending() -> None:
    """Pending migrations should make readiness fail with remediation guidance."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    pending = cli_main.MigrationService(
        "postgresql://example/db", cli_main.MIGRATIONS_DIR
    ).discover()[:1]
    status = MigrationStatus(
        applied=[],
        pending=pending,
        failed=[],
        checksum_drifted=[],
        pgmq_available=True,
        pgmq_installed=True,
        missing_queues=[],
    )

    with (
        patch("netengine.cli.main.run_preflight", return_value=[]),
        patch("netengine.cli.main.MigrationService") as mock_service_class,
    ):
        mock_service_class.return_value.status = AsyncMock(return_value=status)

        result = CliRunner().invoke(
            cli_main.cli,
            ["readiness", str(spec_file), "--db-url", "postgresql://example/db"],
        )

    assert result.exit_code == 1
    assert "[FAIL] migrations:pending: 1 pending migration(s)" in result.output
    assert "Hint: Run `netengine migrate up` before booting." in result.output


def test_readiness_warns_for_experimental_feature_state(tmp_path: Path) -> None:
    """Experimental active feature-state fields should warn but not fail readiness."""
    spec_file = _write_cli_validate_spec(tmp_path, pki__intermediate_ca_enabled=True)

    with (
        patch("netengine.cli.main.run_preflight", return_value=[]),
        patch("netengine.cli.main.MigrationService") as mock_service_class,
    ):
        mock_service_class.return_value.status = AsyncMock(return_value=_healthy_migration_status())

        result = CliRunner().invoke(
            cli_main.cli,
            ["readiness", str(spec_file), "--db-url", "postgresql://example/db"],
        )

    assert result.exit_code == 0, result.output
    assert "warn" in result.output
    assert "[WARN] feature-state: pki.intermediate_ca_enabled: experimental" in result.output
    assert "Review alpha/experimental behavior" in result.output


def test_readiness_fails_when_database_url_missing() -> None:
    """Migration readiness should require a configured database URL."""
    spec_file = Path(__file__).parent.parent / "examples" / "minimal.yaml"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("netengine.cli.main.run_preflight", return_value=[]),
    ):
        result = CliRunner().invoke(cli_main.cli, ["readiness", str(spec_file)])

    assert result.exit_code == 1
    assert (
        "[FAIL] migrations:database-url: NETENGINE_DB_URL/DATABASE_URL is not set" in result.output
    )
    assert "Set NETENGINE_DB_URL or DATABASE_URL" in result.output
