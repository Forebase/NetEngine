"""Host preflight checks for the NetEngine CLI."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import click
import yaml

from netengine.core.state import get_state_file


class DoctorStatus(StrEnum):
    """Status values emitted by the doctor command."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class DoctorCheckResult:
    """Single preflight check result."""

    name: str
    status: DoctorStatus
    detail: str
    hint: str | None = None
    group: str = "general"
    required: bool = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(command: list[str], *, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _compose_config() -> dict:
    compose_file = _repo_root() / "docker-compose.yml"
    try:
        return yaml.safe_load(compose_file.read_text()) or {}
    except Exception:
        return {}


def _compose_ports_and_resources() -> tuple[set[tuple[int, str]], set[str], set[str], set[str]]:
    config = _compose_config()
    ports: set[tuple[int, str]] = set()
    containers: set[str] = set()
    volumes: set[str] = set((config.get("volumes") or {}).keys())
    networks: set[str] = set((config.get("networks") or {}).keys())
    for service_name, service in (config.get("services") or {}).items():
        containers.add(service.get("container_name") or f"netengine-{service_name}-1")
        for volume in service.get("volumes") or []:
            if isinstance(volume, str) and volume and not volume.startswith((".", "/", "~")):
                volumes.add(volume.split(":", 1)[0])
        for port in service.get("ports") or []:
            raw = str(port)
            published = raw.split(":", 1)[0] if ":" in raw else raw
            if published.isdigit():
                ports.add((int(published), "tcp"))
    ports.update({(5432, "tcp"), (53, "tcp"), (53, "udp")})
    return ports, containers, volumes, networks


def _check_python() -> DoctorCheckResult:
    ok = sys.version_info >= (3, 13)
    return DoctorCheckResult(
        "Python runtime",
        DoctorStatus.OK if ok else DoctorStatus.FAIL,
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; pyproject requires ^3.13",
        None if ok else "Install Python 3.13 or newer and recreate the virtualenv.",
        "host",
    )


def _check_command(name: str, *, required: bool = True) -> DoctorCheckResult:
    import shutil

    path = shutil.which(name)
    return DoctorCheckResult(
        f"command:{name}",
        DoctorStatus.OK if path else (DoctorStatus.FAIL if required else DoctorStatus.SKIP),
        path or "not found on PATH",
        None if path else f"Install {name} or add it to PATH.",
        "host",
        required,
    )


def _check_docker_daemon() -> DoctorCheckResult:
    try:
        result = _run(["docker", "info"])
    except Exception as exc:
        return DoctorCheckResult(
            "Docker daemon",
            DoctorStatus.FAIL,
            str(exc),
            "Start Docker and verify socket access.",
            "docker",
        )
    if result.returncode == 0:
        return DoctorCheckResult(
            "Docker daemon", DoctorStatus.OK, "docker info succeeded", group="docker"
        )
    return DoctorCheckResult(
        "Docker daemon",
        DoctorStatus.FAIL,
        (result.stderr or result.stdout).strip() or "docker info failed",
        "Start Docker and verify the current user can access it.",
        "docker",
    )


def _check_compose() -> DoctorCheckResult:
    try:
        result = _run(["docker", "compose", "version"])
    except Exception as exc:
        return DoctorCheckResult(
            "Docker Compose",
            DoctorStatus.FAIL,
            str(exc),
            "Install the Docker Compose plugin.",
            "docker",
        )
    if result.returncode == 0:
        return DoctorCheckResult(
            "Docker Compose",
            DoctorStatus.OK,
            (result.stdout or "available").strip(),
            group="docker",
        )
    return DoctorCheckResult(
        "Docker Compose",
        DoctorStatus.FAIL,
        (result.stderr or result.stdout).strip() or "docker compose version failed",
        "Install or enable Docker Compose v2.",
        "docker",
    )


def _parse_db_url(db_url: str | None) -> DoctorCheckResult:
    if not db_url:
        return DoctorCheckResult(
            "Database URL",
            DoctorStatus.FAIL,
            "NETENGINE_DB_URL/DATABASE_URL is not set",
            "Set NETENGINE_DB_URL=postgresql://netengine:dev_password@localhost:5432/netengine",
            "database",
        )
    parsed = urlparse(db_url)
    ok = (
        parsed.scheme in {"postgres", "postgresql"}
        and bool(parsed.hostname)
        and bool(parsed.path.strip("/"))
    )
    return DoctorCheckResult(
        "Database URL",
        DoctorStatus.OK if ok else DoctorStatus.FAIL,
        "parseable PostgreSQL URL" if ok else "not a valid PostgreSQL URL",
        None if ok else "Use a postgresql:// URL with host and database name.",
        "database",
    )


def _check_psql(db_url: str, sql: str, name: str, *, hint: str) -> DoctorCheckResult:
    try:
        result = _run(["psql", db_url, "-tAc", sql])
    except Exception as exc:
        return DoctorCheckResult(name, DoctorStatus.FAIL, str(exc), hint, "database")
    if result.returncode == 0:
        detail = (result.stdout or "ok").strip()
        if name == "pgmq extension" and detail != "pgmq":
            return DoctorCheckResult(
                name, DoctorStatus.FAIL, "pgmq extension is not installed", hint, "database"
            )
        return DoctorCheckResult(name, DoctorStatus.OK, detail, group="database")
    return DoctorCheckResult(
        name,
        DoctorStatus.FAIL,
        (result.stderr or result.stdout).strip() or "psql command failed",
        hint,
        "database",
    )


def _check_dir_writable(path: Path, name: str) -> DoctorCheckResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".netengine-doctor-", delete=True):
            pass
        return DoctorCheckResult(name, DoctorStatus.OK, f"writable: {path}", group="filesystem")
    except Exception as exc:
        return DoctorCheckResult(
            name,
            DoctorStatus.FAIL,
            f"not writable: {path} ({exc})",
            "Fix directory ownership or choose a writable path.",
            "filesystem",
        )


def _check_state_file(path: Path) -> DoctorCheckResult:
    if not path.exists():
        return DoctorCheckResult(
            "State file", DoctorStatus.OK, f"no existing state at {path}", group="filesystem"
        )
    try:
        data = json.loads(path.read_text())
        phases = data.get("phase_completed", {}) if isinstance(data, dict) else {}
        return DoctorCheckResult(
            "State file",
            DoctorStatus.WARN,
            f"existing state file with {len(phases)} completed phase flag(s): {path}",
            "Remove or back up the state file before bootstrapping a new world.",
            "filesystem",
            required=False,
        )
    except Exception as exc:
        return DoctorCheckResult(
            "State file",
            DoctorStatus.WARN,
            f"existing unreadable/corrupt state file: {exc}",
            "Inspect or remove the state file before continuing.",
            "filesystem",
            required=False,
        )


def _can_bind(port: int, proto: str) -> bool:
    typ = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, typ) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    return True


def _check_port(port: int, proto: str) -> DoctorCheckResult:
    try:
        _can_bind(port, proto)
        return DoctorCheckResult(
            f"port:{port}/{proto}", DoctorStatus.OK, "available on 127.0.0.1", group="ports"
        )
    except PermissionError as exc:
        return DoctorCheckResult(
            f"port:{port}/{proto}",
            DoctorStatus.WARN,
            f"permission denied while probing: {exc}",
            "Run with privileges or check listeners manually.",
            "ports",
            required=False,
        )
    except OSError as exc:
        return DoctorCheckResult(
            f"port:{port}/{proto}",
            DoctorStatus.FAIL,
            f"unavailable on 127.0.0.1: {exc}",
            f"Stop the process using {port}/{proto} or change the compose/spec port.",
            "ports",
        )


def _docker_names(kind: str) -> set[str]:
    try:
        if kind == "container":
            result = _run(["docker", "ps", "-a", "--format", "{{.Names}}"])
        elif kind == "volume":
            result = _run(["docker", "volume", "ls", "--format", "{{.Name}}"])
        else:
            result = _run(["docker", "network", "ls", "--format", "{{.Name}}"])
    except Exception:
        return set()
    return (
        {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if result.returncode == 0
        else set()
    )


def _check_docker_conflicts() -> list[DoctorCheckResult]:
    _, containers, volumes, networks = _compose_ports_and_resources()
    checks = []
    for kind, expected in (("container", containers), ("volume", volumes), ("network", networks)):
        conflicts = sorted(expected & _docker_names(kind))
        checks.append(
            DoctorCheckResult(
                f"Docker {kind} names",
                DoctorStatus.WARN if conflicts else DoctorStatus.OK,
                ", ".join(conflicts) if conflicts else "no known name conflicts",
                (
                    "Run `netengine down` or remove stale Docker resources if these belong to an old run."
                    if conflicts
                    else None
                ),
                "docker",
                required=False,
            )
        )
    return checks


def run_checks(
    db_url: str | None, state_file: Path, *, skip_db: bool = False
) -> list[DoctorCheckResult]:
    checks = [
        _check_python(),
        _check_command("docker"),
        _check_command("psql"),
        _check_command("step", required=False),
        _check_docker_daemon(),
        _check_compose(),
    ]
    if skip_db:
        checks.append(
            DoctorCheckResult(
                "Database checks",
                DoctorStatus.SKIP,
                "--skip-db requested",
                group="database",
                required=False,
            )
        )
    else:
        parsed = _parse_db_url(db_url)
        checks.append(parsed)
        if db_url and parsed.status == DoctorStatus.OK:
            checks.append(
                _check_psql(
                    db_url,
                    "SELECT 1;",
                    "Postgres connectivity",
                    hint="Start Postgres or fix NETENGINE_DB_URL.",
                )
            )
            checks.append(
                _check_psql(
                    db_url,
                    "SELECT extname FROM pg_extension WHERE extname = 'pgmq';",
                    "pgmq extension",
                    hint="Install/enable pgmq in the configured database.",
                )
            )
    ports, _, _, _ = _compose_ports_and_resources()
    checks.extend(_check_port(port, proto) for port, proto in sorted(ports))
    checks.extend(
        [
            _check_dir_writable(state_file.parent, "State directory"),
            _check_dir_writable(Path.home() / ".netengine", "User runtime directory"),
            _check_state_file(state_file),
        ]
    )
    checks.extend(_check_docker_conflicts())
    return checks


def _print_report(results: Iterable[DoctorCheckResult]) -> None:
    by_group: dict[str, list[DoctorCheckResult]] = {}
    for result in results:
        by_group.setdefault(result.group, []).append(result)
    symbols = {
        DoctorStatus.OK: "✓",
        DoctorStatus.WARN: "!",
        DoctorStatus.FAIL: "✗",
        DoctorStatus.SKIP: "-",
    }
    for group, group_results in by_group.items():
        click.echo(f"\n{group.title()}")
        for result in group_results:
            click.echo(f"  {symbols[result.status]} {result.name}: {result.detail}")
            if result.hint and result.status != DoctorStatus.OK:
                click.echo(f"      → {result.hint}")


@click.command("doctor")
@click.option(
    "--db-url",
    default=lambda: os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL"),
    help="PostgreSQL URL (defaults to NETENGINE_DB_URL or DATABASE_URL).",
)
@click.option(
    "--state-file",
    type=click.Path(path_type=Path),
    default=get_state_file,
    help="Runtime state file path.",
)
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
@click.option("--skip-db", is_flag=True, help="Skip database connectivity and pgmq checks.")
def doctor(db_url: str | None, state_file: Path, as_json: bool, skip_db: bool) -> None:
    """Run local host preflight checks before bootstrapping a world."""
    results = run_checks(db_url, state_file, skip_db=skip_db)
    if as_json:
        click.echo(json.dumps([asdict(r) for r in results], indent=2))
    else:
        click.echo("NetEngine doctor preflight report")
        _print_report(results)
    if any(r.status == DoctorStatus.FAIL and r.required for r in results):
        raise click.ClickException("required doctor checks failed")
