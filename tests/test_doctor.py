"""Doctor command tests."""

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from netengine.cli import doctor as doctor_mod
from netengine.cli import main as cli_main
from netengine.cli.doctor import DoctorCheckResult, DoctorStatus
from netengine.diagnostic.preflight import DoctorContext, run_preflight


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


def test_preflight_probe_accepts_doctor_context_without_spec(tmp_path: Path) -> None:
    ctx = DoctorContext(
        db_url=None,
        state_file=tmp_path / "state.json",
        project_root=tmp_path,
        required_ports=(),
        required_commands=(),
        optional_commands=(),
        feature_flags={},
    )

    def probe(context: DoctorContext) -> DoctorCheckResult:
        assert context.project_root == tmp_path
        return DoctorCheckResult("custom", DoctorStatus.OK, "ready")

    results = run_preflight(ctx, probes=[probe])

    assert results == [DoctorCheckResult("custom", DoctorStatus.OK, "ready")]


def test_compose_inventory_includes_known_alpha_ports_and_resources() -> None:
    ports, containers, volumes, _ = doctor_mod._preflight._compose_ports_and_resources()

    assert (5432, "tcp") in ports
    assert (53, "tcp") in ports
    assert (53, "udp") in ports
    assert (8180, "tcp") in ports
    assert (8080, "tcp") in ports
    assert "netengine_postgres" in containers
    assert "netengine_api" in containers
    assert "postgres_data" in volumes


def test_dns_udp_bind_failure_has_local_resolver_hint(monkeypatch) -> None:
    def fake_can_bind(port: int, proto: str) -> bool:
        assert (port, proto) == (53, "udp")
        raise OSError("address already in use")

    monkeypatch.setattr(doctor_mod, "_can_bind", fake_can_bind)
    doctor_mod._preflight._can_bind = fake_can_bind

    result = doctor_mod._preflight._check_port(53, "udp")

    assert result.status == DoctorStatus.FAIL
    assert result.required is True
    assert result.hint is not None
    assert "systemd-resolved" in result.hint
    assert "dnsmasq" in result.hint


def test_docker_resource_conflicts_use_short_format_commands(monkeypatch, tmp_path: Path) -> None:
    commands = []

    def fake_run(command: list[str], *, timeout: float = 8.0):
        commands.append((command, timeout))
        joined = " ".join(command)
        if joined.startswith("docker ps"):
            return SimpleNamespace(returncode=0, stdout="netengine_postgres\n", stderr="")
        if joined.startswith("docker volume ls"):
            return SimpleNamespace(returncode=0, stdout="netengine_data\n", stderr="")
        if joined.startswith("docker network ls"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(doctor_mod._preflight, "_run", fake_run)
    ctx = DoctorContext(
        db_url=None,
        state_file=tmp_path / "state.json",
        project_root=tmp_path,
        required_ports=(),
        required_commands=(),
        optional_commands=(),
        feature_flags={},
    )

    results = doctor_mod._preflight._check_docker_conflicts(ctx)

    container_check = next(r for r in results if r.name == "Docker container names")
    volume_check = next(r for r in results if r.name == "Docker volume names")
    assert container_check.status == DoctorStatus.FAIL
    assert container_check.required is True
    assert volume_check.status == DoctorStatus.WARN
    assert volume_check.required is False
    assert (["docker", "ps", "--format", "{{.Names}}"], 2.0) in commands
    assert (["docker", "volume", "ls", "--format", "{{.Name}}"], 2.0) in commands
    assert (["docker", "network", "ls", "--format", "{{.Name}}"], 2.0) in commands
