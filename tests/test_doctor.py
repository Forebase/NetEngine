"""Doctor command tests."""

import sys
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from netengine.cli import doctor as doctor_mod
from netengine.cli import main as cli_main
from netengine.cli.doctor import DoctorCheckResult, DoctorStatus
from netengine.diagnostic.preflight import DoctorContext, run_preflight
from netengine.events.queues import PRIMARY_QUEUES, dlq_for


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


def test_run_checks_uses_asyncpg_for_connectivity_pgmq_and_queues(
    monkeypatch, tmp_path: Path
) -> None:
    calls = []
    expected_queues = [q.value for q in PRIMARY_QUEUES] + [dlq_for(q).value for q in PRIMARY_QUEUES]

    class FakeConnection:
        async def fetchval(self, sql: str):
            calls.append(("fetchval", sql))
            if sql == "SELECT 1;":
                return 1
            if sql == "SELECT 1 FROM pg_extension WHERE extname = 'pgmq';":
                return 1
            raise AssertionError(sql)

        async def fetch(self, sql: str):
            calls.append(("fetch", sql))
            assert sql == "SELECT queue_name FROM pgmq.list_queues();"
            return [{"queue_name": name} for name in expected_queues]

        async def close(self):
            calls.append(("close", None))

    async def fake_connect(db_url: str, *, timeout: float):
        calls.append(("connect", db_url, timeout))
        return FakeConnection()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=fake_connect))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        doctor_mod,
        "_run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    monkeypatch.setattr(doctor_mod, "_can_bind", lambda port, proto: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    results = doctor_mod.run_checks(
        "postgresql://netengine:dev@localhost:5432/netengine",
        tmp_path / "state.json",
        skip_db=False,
    )

    assert any(r.name == "Postgres connectivity" and r.status == DoctorStatus.OK for r in results)
    assert any(r.name == "pgmq extension" and r.status == DoctorStatus.OK for r in results)
    assert any(r.name == "pgmq queues" and r.status == DoctorStatus.OK for r in results)
    assert ("connect", "postgresql://netengine:dev@localhost:5432/netengine", 3.0) in calls
    assert ("fetchval", "SELECT 1 FROM pg_extension WHERE extname = 'pgmq';") in calls
    assert ("fetch", "SELECT queue_name FROM pgmq.list_queues();") in calls


def test_doctor_missing_db_url_prints_actionable_remediation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NETENGINE_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def focused_checks(db_url: str | None, state_file: Path, *, skip_db: bool):
        assert db_url is None
        return doctor_mod._preflight._check_database(
            DoctorContext(
                db_url=db_url,
                state_file=state_file,
                project_root=tmp_path,
                required_ports=(),
                required_commands=(),
                optional_commands=(),
                feature_flags={"skip_db": skip_db},
            )
        )

    monkeypatch.setattr(doctor_mod, "run_checks", focused_checks)

    result = CliRunner().invoke(
        cli_main.cli, ["doctor", "--state-file", str(tmp_path / "state.json")]
    )

    assert result.exit_code == 1
    assert "NETENGINE_DB_URL/DATABASE_URL is not set" in result.output
    assert "docker compose up -d db" in result.output
    assert (
        "export NETENGINE_DB_URL=postgresql://netengine:dev_password@localhost:5432/netengine"
        in result.output
    )


def test_doctor_warns_when_pgmq_queues_are_missing(monkeypatch, tmp_path: Path) -> None:
    class FakeConnection:
        async def fetchval(self, sql: str):
            return 1

        async def fetch(self, sql: str):
            return []

        async def close(self):
            pass

    async def fake_connect(db_url: str, *, timeout: float):
        return FakeConnection()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=fake_connect))

    result = doctor_mod._preflight._check_database(
        DoctorContext(
            db_url="postgresql://netengine:dev@localhost:5432/netengine",
            state_file=tmp_path / "state.json",
            project_root=tmp_path,
            required_ports=(),
            required_commands=(),
            optional_commands=(),
            feature_flags={},
        )
    )

    queue_check = next(r for r in result if r.name == "pgmq queues")
    assert queue_check.status == DoctorStatus.WARN
    assert queue_check.required is False
    assert "missing queues:" in queue_check.detail
    assert "netengine migrate up" in (queue_check.hint or "")


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
