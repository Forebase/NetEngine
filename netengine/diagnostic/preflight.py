"""Host-readiness preflight probes for NetEngine.

Doctor/preflight checks validate local prerequisites before a world is
bootstrapped: host commands, Docker availability, bindable ports, writable
runtime paths, and optional database prerequisites. These probes intentionally
operate on :class:`DoctorContext` instead of ``NetEngineSpec`` so ``netengine
doctor`` can run before any spec is loaded.

By contrast, :mod:`netengine.diagnostic.runner` probes validate health of an
already configured/running world and require a loaded ``NetEngineSpec``.
"""

from __future__ import annotations

import importlib
import json
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple
from urllib.parse import urlparse

import click


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


@dataclass(frozen=True)
class DoctorContext:
    """Input for host-readiness probes, independent of ``NetEngineSpec``."""

    db_url: str | None
    state_file: Path
    project_root: Path
    required_ports: tuple[tuple[int, str], ...]
    required_commands: tuple[str, ...] = ("docker",)
    optional_commands: tuple[str, ...] = ("step",)
    feature_flags: dict[str, bool] | None = None
    spec_subnets: tuple[str, ...] = ()


DoctorProbe = Callable[[DoctorContext], DoctorCheckResult | Iterable[DoctorCheckResult]]


class KnownPort(NamedTuple):
    """A local host port NetEngine alpha may need to bind."""

    port: int
    proto: str
    label: str


class DockerPortBinding(NamedTuple):
    """A Docker container binding published onto the local host."""

    container: str
    container_port: int
    proto: str
    host_port: int
    host_ip: str | None


# Inventory of alpha resources that are known even when compose metadata cannot be read.
# Keep these in sync with docker-compose.yml and runtime-managed DNS resources.
KNOWN_LOCAL_PORTS: tuple[KnownPort, ...] = (
    KnownPort(5432, "tcp", "Postgres"),
    KnownPort(53, "tcp", "DNS"),
    KnownPort(53, "udp", "DNS"),
    KnownPort(8180, "tcp", "Keycloak/OIDC"),
    KnownPort(8080, "tcp", "NetEngine API"),
)

KNOWN_DOCKER_CONTAINERS: frozenset[str] = frozenset(
    {"netengine_postgres", "netengine_keycloak", "netengine_api"}
)
KNOWN_DOCKER_VOLUMES: frozenset[str] = frozenset(
    {"postgres_data", "netengine_data", "netengines_pki_data"}
)
KNOWN_DOCKER_NETWORKS: frozenset[str] = frozenset()


PYTHON_DEPENDENCY_MODULES: tuple[tuple[str, str], ...] = (
    ("pydantic", "pydantic"),
    ("pyyaml", "yaml"),
    ("loguru", "loguru"),
    ("docker", "docker"),
    ("aiohttp", "aiohttp"),
    ("dnspython", "dns"),
    ("asyncpg", "asyncpg"),
    ("fastapi", "fastapi"),
    ("omegaconf", "omegaconf"),
    ("prometheus-client", "prometheus_client"),
)

_DNS_PORT_HINT = (
    "Port 53 is commonly held by a local DNS resolver. Stop or reconfigure systemd-resolved, "
    "dnsmasq, named/CoreDNS, or another DNS server. On macOS with Docker Desktop, "
    "binding privileged host port 53 may require elevated privileges or a configurable "
    "alternate DNS host port."
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(
    command: list[str], *, timeout: float = 8.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )


def _compose_config(project_root: Path | None = None) -> dict[str, Any]:
    compose_file = (project_root or _repo_root()) / "docker-compose.yml"
    try:
        yaml = importlib.import_module("yaml")
        return yaml.safe_load(compose_file.read_text()) or {}
    except Exception:
        return {}


def _parse_compose_port(raw_port: object) -> tuple[int, str] | None:
    """Return the published host port/protocol from a compose port entry."""
    if isinstance(raw_port, int):
        return raw_port, "tcp"
    if isinstance(raw_port, dict):
        published = raw_port.get("published") or raw_port.get("target")
        if published is None:
            return None
        proto = str(raw_port.get("protocol") or "tcp").lower()
        if published is None:
            return None
        try:
            return int(published), proto
        except (TypeError, ValueError):
            return None

    raw = str(raw_port).strip()
    if not raw:
        return None
    port_part, _, proto_part = raw.partition("/")
    proto = (proto_part or "tcp").lower()
    published = port_part.rsplit(":", 1)[0] if ":" in port_part else port_part
    if published.isdigit():
        return int(published), proto
    return None


def _compose_ports_and_resources(
    project_root: Path | None = None,
) -> tuple[set[tuple[int, str]], set[str], set[str], set[str]]:
    config = _compose_config(project_root)
    ports: set[tuple[int, str]] = {(p.port, p.proto) for p in KNOWN_LOCAL_PORTS}
    containers: set[str] = set(KNOWN_DOCKER_CONTAINERS)
    volumes: set[str] = set(KNOWN_DOCKER_VOLUMES) | set(
        (config.get("volumes") or {}).keys()
    )
    networks: set[str] = set(KNOWN_DOCKER_NETWORKS) | set(
        (config.get("networks") or {}).keys()
    )
    for service_name, service in (config.get("services") or {}).items():
        containers.add(service.get("container_name") or f"netengine-{service_name}-1")
        for volume in service.get("volumes") or []:
            if (
                isinstance(volume, str)
                and volume
                and not volume.startswith((".", "/", "~"))
            ):
                volumes.add(volume.split(":", 1)[0])
        for port in service.get("ports") or []:
            parsed = _parse_compose_port(port)
            if parsed:
                ports.add(parsed)
    return ports, containers, volumes, networks


def _check_python() -> DoctorCheckResult:
    ok = sys.version_info >= (3, 13)
    return DoctorCheckResult(
        "Python runtime",
        DoctorStatus.OK if ok else DoctorStatus.FAIL,
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro};"
        " pyproject requires ^3.13",
        None if ok else "Install Python 3.13 or newer and recreate the virtualenv.",
        "host",
    )


def _check_python_dependencies() -> DoctorCheckResult:
    """Verify that required runtime Python packages can be imported."""
    missing: list[str] = []
    for package_name, module_name in PYTHON_DEPENDENCY_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(f"{package_name} ({module_name})")

    if not missing:
        return DoctorCheckResult(
            "Python dependencies",
            DoctorStatus.OK,
            f"{len(PYTHON_DEPENDENCY_MODULES)} required runtime modules importable",
            group="host",
        )

    return DoctorCheckResult(
        "Python dependencies",
        DoctorStatus.FAIL,
        "missing required runtime module(s): " + ", ".join(missing),
        "Install project dependencies with `poetry install`, then rerun `netengine doctor`.",
        "host",
    )


def _check_command(name: str, *, required: bool = True) -> DoctorCheckResult:
    import shutil

    path = shutil.which(name)
    return DoctorCheckResult(
        f"command:{name}",
        DoctorStatus.OK
        if path
        else (DoctorStatus.FAIL if required else DoctorStatus.SKIP),
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
                name,
                DoctorStatus.FAIL,
                "pgmq extension is not installed",
                hint,
                "database",
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
        with tempfile.NamedTemporaryFile(
            dir=path, prefix=".netengine-doctor-", delete=True
        ):
            pass
        return DoctorCheckResult(
            name, DoctorStatus.OK, f"writable: {path}", group="filesystem"
        )
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
            "State file",
            DoctorStatus.OK,
            f"no existing state at {path}",
            group="filesystem",
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


def _container_display_name(container: dict[str, Any]) -> str:
    names = container.get("Name") or container.get("Names") or []
    if isinstance(names, list) and names:
        names = names[0]
    if isinstance(names, str) and names:
        return names.lstrip("/")
    return str(container.get("Id") or container.get("ID") or "unknown")[:12]


def _published_docker_ports() -> list[DockerPortBinding]:
    """Return host ports published by running Docker containers.

    The doctor uses this only after a bind probe fails, so Docker inspection is
    best-effort: if Docker is unavailable, the original port failure remains the
    actionable result.
    """
    try:
        ps = _run(["docker", "ps", "-q"], timeout=2.0)
    except Exception:
        return []
    if ps.returncode != 0 or not ps.stdout.strip():
        return []

    container_ids = ps.stdout.split()
    try:
        inspected = _run(["docker", "inspect", *container_ids], timeout=3.0)
    except Exception:
        return []
    if inspected.returncode != 0 or not inspected.stdout.strip():
        return []

    try:
        containers = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        return []

    bindings: list[DockerPortBinding] = []
    for container in containers if isinstance(containers, list) else []:
        if not isinstance(container, dict):
            continue
        name = _container_display_name(container)
        ports = (container.get("NetworkSettings") or {}).get("Ports") or {}
        for container_port, host_bindings in ports.items():
            port_text, _, proto = str(container_port).partition("/")
            if not port_text.isdigit() or not proto:
                continue
            for host_binding in host_bindings or []:
                try:
                    host_port = int(host_binding.get("HostPort"))
                except (AttributeError, TypeError, ValueError):
                    continue
                bindings.append(
                    DockerPortBinding(
                        name,
                        int(port_text),
                        proto.lower(),
                        host_port,
                        host_binding.get("HostIp"),
                    )
                )
    return bindings


def _docker_bindings_for_port(port: int, proto: str) -> list[DockerPortBinding]:
    return [
        binding
        for binding in _published_docker_ports()
        if binding.host_port == port and binding.proto == proto.lower()
    ]


def _is_expected_netengine_binding(binding: DockerPortBinding) -> bool:
    return binding.container in KNOWN_DOCKER_CONTAINERS and (
        binding.host_port,
        binding.proto,
    ) in {(p.port, p.proto) for p in KNOWN_LOCAL_PORTS}


def _check_port(port: int, proto: str) -> DoctorCheckResult:
    name = f"port:{port}/{proto}"
    label = next(
        (p.label for p in KNOWN_LOCAL_PORTS if p.port == port and p.proto == proto),
        None,
    )
    detail_suffix = f" ({label})" if label else ""
    try:
        _can_bind(port, proto)
        return DoctorCheckResult(
            name,
            DoctorStatus.OK,
            f"available on 127.0.0.1{detail_suffix}",
            group="ports",
        )
    except PermissionError as exc:
        return DoctorCheckResult(
            name,
            DoctorStatus.WARN,
            f"permission denied while probing{detail_suffix}: {exc}",
            _DNS_PORT_HINT
            if port == 53
            else "Run with privileges or check listeners manually.",
            "ports",
            required=False,
        )
    except OSError as exc:
        docker_bindings = _docker_bindings_for_port(port, proto)
        expected_bindings = [b for b in docker_bindings if _is_expected_netengine_binding(b)]
        if expected_bindings:
            containers = ", ".join(sorted({b.container for b in expected_bindings}))
            return DoctorCheckResult(
                name,
                DoctorStatus.OK,
                f"already bound by {containers}{detail_suffix}",
                group="ports",
            )

        if docker_bindings:
            listeners = ", ".join(
                sorted({f"{b.container} ({b.container_port}/{b.proto})" for b in docker_bindings})
            )
            return DoctorCheckResult(
                name,
                DoctorStatus.FAIL,
                f"unavailable on 127.0.0.1{detail_suffix}: already bound by {listeners}",
                f"Stop the container publishing {port}/{proto} or change the compose/spec port.",
                "ports",
            )

        hint = (
            _DNS_PORT_HINT
            if port == 53
            else f"Stop the process using {port}/{proto} or change the compose/spec port."
        )
        return DoctorCheckResult(
            name,
            DoctorStatus.FAIL,
            f"unavailable on 127.0.0.1{detail_suffix}: {exc}",
            hint,
            "ports",
        )


def _docker_names(kind: str) -> set[str]:
    try:
        if kind == "container":
            result = _run(["docker", "ps", "--format", "{{.Names}}"], timeout=2.0)
        elif kind == "volume":
            result = _run(
                ["docker", "volume", "ls", "--format", "{{.Name}}"], timeout=2.0
            )
        else:
            result = _run(
                ["docker", "network", "ls", "--format", "{{.Name}}"], timeout=2.0
            )
    except Exception:
        return set()
    return (
        {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if result.returncode == 0
        else set()
    )


def _check_docker_subnet_conflicts(ctx: DoctorContext) -> DoctorCheckResult:
    """Warn if any existing Docker network subnets overlap with NetEngine's configured subnets."""
    compose_config = _compose_config(ctx.project_root)
    ne_subnets: list[str] = []
    for network_cfg in (compose_config.get("networks") or {}).values():
        if isinstance(network_cfg, dict):
            for ipam_config in (network_cfg.get("ipam") or {}).get("config") or []:
                if isinstance(ipam_config, dict) and "subnet" in ipam_config:
                    ne_subnets.append(str(ipam_config["subnet"]))
    for subnet in ctx.spec_subnets:
        if subnet not in ne_subnets:
            ne_subnets.append(subnet)

    if not ne_subnets:
        return DoctorCheckResult(
            "Docker subnet conflicts",
            DoctorStatus.SKIP,
            "no subnets defined in compose config or spec",
            group="docker",
            required=False,
        )

    try:
        import ipaddress

        result = _run(["docker", "network", "ls", "-q"])
        if result.returncode != 0 or not result.stdout.strip():
            return DoctorCheckResult(
                "Docker subnet conflicts",
                DoctorStatus.OK,
                "no existing Docker networks to check",
                group="docker",
            )
        network_ids = result.stdout.split()
        inspect_result = _run(["docker", "network", "inspect"] + network_ids)
        if inspect_result.returncode != 0:
            return DoctorCheckResult(
                "Docker subnet conflicts",
                DoctorStatus.WARN,
                "could not inspect Docker networks",
                "Run `docker network ls` manually to check for subnet conflicts.",
                "docker",
                required=False,
            )

        import json as _json

        networks = _json.loads(inspect_result.stdout)
        ne_networks = [ipaddress.ip_network(s, strict=False) for s in ne_subnets]
        conflicts: list[str] = []
        for network in networks:
            name = network.get("Name", "unknown")
            for ipam_config in (network.get("IPAM") or {}).get("Config") or []:
                subnet_str = ipam_config.get("Subnet")
                if not subnet_str:
                    continue
                try:
                    existing = ipaddress.ip_network(subnet_str, strict=False)
                except ValueError:
                    continue
                for ne_net in ne_networks:
                    if existing.overlaps(ne_net):
                        relation = "reuses" if existing == ne_net else "overlaps"
                        conflicts.append(
                            f"Docker network {name} ({subnet_str}) {relation} requested subnet {ne_net}"
                        )

        if conflicts:
            return DoctorCheckResult(
                "Docker subnet conflicts",
                DoctorStatus.WARN,
                "; ".join(conflicts),
                "Remove conflicting Docker networks with"
                " `docker network rm <name>` or choose non-overlapping subnets in the world spec.",
                "docker",
                required=False,
            )
        return DoctorCheckResult(
            "Docker subnet conflicts",
            DoctorStatus.OK,
            f"no conflicts with {', '.join(ne_subnets)}",
            group="docker",
        )
    except Exception as exc:
        return DoctorCheckResult(
            "Docker subnet conflicts",
            DoctorStatus.WARN,
            f"subnet conflict check failed: {exc}",
            "Run `docker network ls` manually to check for subnet conflicts.",
            "docker",
            required=False,
        )


def _check_docker_conflicts(ctx: DoctorContext) -> list[DoctorCheckResult]:
    _, containers, volumes, networks = _compose_ports_and_resources(ctx.project_root)
    checks = []
    for kind, expected in (
        ("container", containers),
        ("volume", volumes),
        ("network", networks),
    ):
        conflicts = sorted(expected & _docker_names(kind))
        checks.append(
            DoctorCheckResult(
                f"Docker {kind} names",
                (
                    DoctorStatus.FAIL
                    if kind == "container" and conflicts
                    else DoctorStatus.WARN
                    if conflicts
                    else DoctorStatus.OK
                ),
                ", ".join(conflicts) if conflicts else "no known name conflicts",
                (
                    "Stop/remove conflicting containers before startup."
                    if kind == "container" and conflicts
                    else (
                        (
                            "Run `netengine down` or remove stale Docker resources if these "
                            "belong to an old run."
                        )
                        if conflicts
                        else None
                    )
                ),
                "docker",
                required=kind == "container",
            )
        )
    return checks


def build_context(
    db_url: str | None,
    state_file: Path,
    *,
    skip_db: bool = False,
    project_root: Path | None = None,
    spec_subnets: tuple[str, ...] = (),
) -> DoctorContext:
    """Build the default doctor context from CLI inputs and compose metadata."""
    root = project_root or _repo_root()
    ports, _, _, _ = _compose_ports_and_resources(root)
    return DoctorContext(
        db_url=db_url,
        state_file=state_file,
        project_root=root,
        required_ports=tuple(sorted(ports)),
        feature_flags={"skip_db": skip_db},
        spec_subnets=spec_subnets,
    )


def _check_required_commands(ctx: DoctorContext) -> list[DoctorCheckResult]:
    return [_check_command(name) for name in ctx.required_commands]


def _check_optional_commands(ctx: DoctorContext) -> list[DoctorCheckResult]:
    return [_check_command(name, required=False) for name in ctx.optional_commands]


def _check_step_version() -> DoctorCheckResult:
    """Check step CLI version for PKI compatibility."""
    import shutil

    step_path = shutil.which("step")
    if not step_path:
        return DoctorCheckResult(
            "step version",
            DoctorStatus.SKIP,
            "step CLI not found; PKI features will be unavailable",
            None,
            "host",
            required=False,
        )

    try:
        result = _run(["step", "version"])
        if result.returncode == 0:
            version_output = result.stdout.strip()
            return DoctorCheckResult(
                "step version",
                DoctorStatus.OK,
                f"step CLI available: {version_output.split()[0] if version_output else 'unknown'}",
                None,
                "host",
                required=False,
            )
    except Exception as exc:
        return DoctorCheckResult(
            "step version",
            DoctorStatus.WARN,
            f"Could not determine step version: {exc}",
            "Ensure step CLI is installed and working correctly.",
            "host",
            required=False,
        )

    return DoctorCheckResult(
        "step version",
        DoctorStatus.WARN,
        "step version check failed",
        "Ensure step CLI is properly installed.",
        "host",
        required=False,
    )


def _check_database(ctx: DoctorContext) -> list[DoctorCheckResult]:
    if (ctx.feature_flags or {}).get("skip_db", False):
        return [
            DoctorCheckResult(
                "Database checks",
                DoctorStatus.SKIP,
                "--skip-db requested",
                group="database",
                required=False,
            )
        ]

    from netengine.diagnostic.db_doctor import check_database

    return check_database(ctx.db_url)


def _check_ports(ctx: DoctorContext) -> list[DoctorCheckResult]:
    return [_check_port(port, proto) for port, proto in ctx.required_ports]


def _check_filesystem(ctx: DoctorContext) -> list[DoctorCheckResult]:
    return [
        _check_dir_writable(ctx.state_file.parent, "State directory"),
        _check_dir_writable(Path.home() / ".netengine", "User runtime directory"),
        _check_state_file(ctx.state_file),
    ]


def _check_pgmq_runtime_state(ctx: DoctorContext) -> DoctorCheckResult:
    from netengine.diagnostic.db_doctor import check_pgmq_runtime_state

    return check_pgmq_runtime_state(ctx.state_file)


def standard_probes() -> tuple[DoctorProbe, ...]:
    """Return host-readiness probes; each accepts only ``DoctorContext``."""
    return (
        lambda ctx: _check_python(),
        lambda ctx: _check_python_dependencies(),
        _check_required_commands,
        _check_optional_commands,
        lambda ctx: _check_step_version(),
        lambda ctx: _check_docker_daemon(),
        lambda ctx: _check_compose(),
        _check_database,
        _check_pgmq_runtime_state,
        _check_ports,
        _check_filesystem,
        _check_docker_conflicts,
        lambda ctx: _check_docker_subnet_conflicts(ctx),
    )


def run_preflight(
    ctx: DoctorContext, probes: Iterable[DoctorProbe] | None = None
) -> list[DoctorCheckResult]:
    """Run host-readiness probes for a doctor context."""
    results: list[DoctorCheckResult] = []
    for probe in probes or standard_probes():
        result = probe(ctx)
        if isinstance(result, DoctorCheckResult):
            results.append(result)
        else:
            results.extend(result)
    return results


def run_checks(
    db_url: str | None,
    state_file: Path,
    *,
    skip_db: bool = False,
    spec_subnets: tuple[str, ...] = (),
) -> list[DoctorCheckResult]:
    """Compatibility wrapper for callers that do not build ``DoctorContext``."""
    return run_preflight(
        build_context(db_url, state_file, skip_db=skip_db, spec_subnets=spec_subnets)
    )


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
