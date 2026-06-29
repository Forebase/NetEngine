"""Doctor command tests."""

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from netengine.cli import doctor as doctor_mod
from netengine.cli import main as cli_main
from netengine.cli.doctor import DoctorCheckResult, DoctorStatus


def test_doctor_appears_in_help() -> None:
    result = CliRunner().invoke(cli_main.cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "doctor" in result.output
    assert "Run local host preflight checks" in result.output


def test_doctor_json_uses_env_db_url_and_returns_nonzero_on_required_failure(
    monkeypatch, tmp_path: Path
) -> None:
    captured = {}

    def fake_run_checks(db_url: str | None, state_file: Path, *, skip_db: bool):
        captured["db_url"] = db_url
        captured["state_file"] = state_file
        captured["skip_db"] = skip_db
        return [
            DoctorCheckResult("Python runtime", DoctorStatus.OK, "ok", group="host"),
            DoctorCheckResult("Docker daemon", DoctorStatus.FAIL, "not reachable", group="docker"),
        ]

    state_file = tmp_path / "state.json"
    monkeypatch.setenv("NETENGINE_DB_URL", "postgresql://netengine:dev@localhost:5432/netengine")
    monkeypatch.setattr(doctor_mod, "run_checks", fake_run_checks)

    result = CliRunner().invoke(cli_main.cli, ["doctor", "--json", "--state-file", str(state_file)])

    assert result.exit_code == 1
    assert '"status": "fail"' in result.output
    assert captured == {
        "db_url": "postgresql://netengine:dev@localhost:5432/netengine",
        "state_file": state_file,
        "skip_db": False,
    }


def test_doctor_skip_db_succeeds_when_only_db_checks_are_skipped(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run_checks(db_url: str | None, state_file: Path, *, skip_db: bool):
        assert skip_db is True
        return [
            DoctorCheckResult(
                "Database checks",
                DoctorStatus.SKIP,
                "--skip-db requested",
                group="database",
                required=False,
            )
        ]

    monkeypatch.setattr(doctor_mod, "run_checks", fake_run_checks)

    result = CliRunner().invoke(
        cli_main.cli, ["doctor", "--skip-db", "--state-file", str(tmp_path / "state.json")]
    )

    assert result.exit_code == 0, result.output
    assert "Database checks" in result.output


def test_run_checks_invokes_psql_for_connectivity_and_pgmq(monkeypatch, tmp_path: Path) -> None:
    commands = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    def fake_run(command: list[str], *, timeout: float = 8.0):
        commands.append(command)
        stdout = "pgmq\n" if "pg_extension" in " ".join(command) else "1\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(doctor_mod, "_run", fake_run)
    monkeypatch.setattr(doctor_mod, "_can_bind", lambda port, proto: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    results = doctor_mod.run_checks(
        "postgresql://netengine:dev@localhost:5432/netengine",
        tmp_path / "state.json",
        skip_db=False,
    )

    assert any(r.name == "Postgres connectivity" and r.status == DoctorStatus.OK for r in results)
    assert any(r.name == "pgmq extension" and r.status == DoctorStatus.OK for r in results)
    assert [
        "psql",
        "postgresql://netengine:dev@localhost:5432/netengine",
        "-tAc",
        "SELECT 1;",
    ] in commands
    assert [
        "psql",
        "postgresql://netengine:dev@localhost:5432/netengine",
        "-tAc",
        "SELECT extname FROM pg_extension WHERE extname = 'pgmq';",
    ] in commands


def test_state_file_conflict_is_warning(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"phase_completed": {"0": true}}')

    result = doctor_mod._check_state_file(state_file)

    assert result.status == DoctorStatus.WARN
    assert result.required is False
    assert "existing state file" in result.detail
