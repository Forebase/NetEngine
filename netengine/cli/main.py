"""NetEngine CLI — operator surface for world management."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from netengine.cli.doctor import doctor
from netengine.diagnostic.preflight import (
    DoctorCheckResult,
    DoctorStatus,
    build_context,
    run_preflight,
)
from netengine.cli.env import db_url_from_env
from netengine.core.migrations import MigrationService, MigrationStatus
from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState, get_state_file
from netengine.db.migrations import (
    MIGRATIONS_DIR,
    MigrationRunResult,
    migration_status,
    run_migrations,
)
from netengine.events.queues import PRIMARY_QUEUES, Queue, dlq_for
from netengine.logs import get_logger
from netengine.phase_labels import PHASE_LABELS
from netengine.spec.loader import (
    SpecLoadError,
    _is_active_feature_value,
    _resolve_feature_state_paths,
    load_spec,
    load_spec_with_composition,
    load_spec_with_environment,
)

logger = get_logger(__name__)


def _parse_set_overrides(set_values: tuple[str, ...]) -> dict[str, Any]:
    """Convert repeatable dotted key=value CLI overrides into a nested dictionary."""
    overrides: dict[str, Any] = {}

    for item in set_values:
        key, separator, raw_value = item.partition("=")
        if not separator:
            raise click.BadParameter("must be in key=value form", param_hint="--set")

        parts = key.split(".")
        if any(part == "" for part in parts):
            raise click.BadParameter(
                "keys must be non-empty dotted paths", param_hint="--set"
            )

        value = yaml.safe_load(raw_value)
        cursor = overrides
        for part in parts[:-1]:
            existing = cursor.get(part)
            if existing is None:
                nested: dict[str, Any] = {}
                cursor[part] = nested
                cursor = nested
            elif isinstance(existing, dict):
                cursor = existing
            else:
                raise click.BadParameter(
                    f"cannot set nested key under non-object path '{part}'",
                    param_hint="--set",
                )
        cursor[parts[-1]] = value

    return overrides


def _load_spec_for_cli(
    spec_file: str,
    *,
    environment: str | None = None,
    set_values: tuple[str, ...] = (),
    validate_feature_states: bool = True,
):
    """Load a spec using the same composition semantics as ``up``."""
    overrides = _parse_set_overrides(set_values)
    feature_state_kwargs = (
        {} if validate_feature_states else {"validate_feature_states": False}
    )
    if environment:
        return load_spec_with_environment(
            spec_file,
            environment=environment,
            overrides=overrides or None,
            **feature_state_kwargs,
        )
    if overrides:
        return load_spec_with_composition(
            spec_file, overrides=overrides, **feature_state_kwargs
        )
    return load_spec(spec_file, **feature_state_kwargs)


def _feature_state_explanations(spec: Any) -> list[str]:
    """Return noteworthy active feature-state lines for ``validate --explain``."""
    lines: list[str] = []
    for entry, path, value, default_value in _resolve_feature_state_paths(spec):
        if not _is_active_feature_value(value, default_value):
            continue
        value_text = getattr(value, "value", value)
        lines.append(
            f"{path}: {entry.state} ({entry.stage}) - {entry.reason}; value={value_text!r}"
        )
    return lines


def _jsonable_feature_value(value: Any) -> Any:
    """Return a JSON-serializable representation for feature-state values."""
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _active_feature_state_results(spec: Any) -> list[dict[str, Any]]:
    """Return machine-readable active feature-state validation results."""
    results: list[dict[str, Any]] = []
    for entry, path, value, default_value in _resolve_feature_state_paths(spec):
        if not _is_active_feature_value(value, default_value):
            continue
        results.append(
            {
                "path": path,
                "state": entry.state,
                "stage": entry.stage,
                "reason": entry.reason,
                "current_value": _jsonable_feature_value(value),
                "default_value": _jsonable_feature_value(default_value),
            }
        )
    return results


async def _run_migrations(db_url: str) -> MigrationRunResult:
    """Run SQL migrations using the shared migration service."""
    result = await run_migrations(db_url)
    for migration in result.results:
        if migration.status == "applied":
            logger.info(
                f"Applied migration: {migration.filename} "
                f"({migration.duration_seconds:.3f}s)"
            )
        elif migration.status == "skipped":
            logger.info(f"Skipped migration: {migration.filename} (already applied)")
    logger.info(
        f"Migrations complete: {result.applied_count} applied, "
        f"{result.skipped_count} skipped, {result.failed_count} failed"
    )
    return result


def _db_url_from_env() -> str | None:
    """Return the database URL used by CLI migration operations."""
    return db_url_from_env()


def _require_db_url() -> str:
    """Return the configured DB URL or exit with migration-oriented guidance."""
    db_url = _db_url_from_env()
    if not db_url:
        click.echo("No database URL configured for migrations", err=True)
        sys.exit(2)
    return db_url


def _print_migration_status(status: MigrationStatus) -> None:
    click.echo("Migration status")
    click.echo(f"  Applied: {len(status.applied)}")
    for record in status.applied:
        applied_at = (
            record.applied_at.isoformat() if record.applied_at else "unknown time"
        )
        click.echo(f"    ✓ {record.version} {record.name} ({applied_at})")

    click.echo(f"  Pending: {len(status.pending)}")
    for migration in status.pending:
        click.echo(
            f"    • {migration.version} {migration.name} ({migration.path.name})"
        )

    click.echo(f"  Failed: {len(status.failed)}")
    for record in status.failed:
        detail = f": {record.error}" if record.error else ""
        click.echo(f"    ✗ {record.version} {record.name}{detail}")

    click.echo(f"  Checksum drift: {len(status.checksum_drifted)}")
    for migration, record in status.checksum_drifted:
        click.echo(
            f"    ! {migration.version} {migration.name}: "
            f"database={record.checksum or 'unknown'} file={migration.checksum}"
        )

    click.echo("  pgmq prerequisites:")
    click.echo(f"    Extension available: {'yes' if status.pgmq_available else 'no'}")
    click.echo(f"    Extension installed: {'yes' if status.pgmq_installed else 'no'}")
    if status.missing_queues:
        click.echo(f"    Missing queues: {', '.join(status.missing_queues)}")
    else:
        click.echo("    Missing queues: none")


_STATUS_LABELS = {
    DoctorStatus.OK: "PASS",
    DoctorStatus.WARN: "WARN",
    DoctorStatus.FAIL: "FAIL",
    DoctorStatus.SKIP: "SKIP",
}


def _readiness_line(
    status: DoctorStatus, name: str, detail: str, hint: str | None = None
) -> None:
    """Print one readiness check line with an optional remediation hint."""
    click.echo(f"[{_STATUS_LABELS[status]}] {name}: {detail}")
    if hint and status in {DoctorStatus.WARN, DoctorStatus.FAIL}:
        click.echo(f"       Hint: {hint}")


def _migration_readiness_results(status: MigrationStatus) -> list[DoctorCheckResult]:
    """Convert migration status into concise readiness check results."""
    results = [
        DoctorCheckResult(
            "migrations:pending",
            DoctorStatus.FAIL if status.pending else DoctorStatus.OK,
            f"{len(status.pending)} pending migration(s)",
            "Run `netengine migrate up` before booting." if status.pending else None,
            "migrations",
        ),
        DoctorCheckResult(
            "migrations:failed",
            DoctorStatus.FAIL if status.failed else DoctorStatus.OK,
            f"{len(status.failed)} failed migration record(s)",
            "Inspect netengine_schema_migrations errors and rerun migrations."
            if status.failed
            else None,
            "migrations",
        ),
        DoctorCheckResult(
            "migrations:checksum-drift",
            DoctorStatus.FAIL if status.checksum_drifted else DoctorStatus.OK,
            f"{len(status.checksum_drifted)} drifted checksum(s)",
            "Restore the expected migration files or reconcile database history."
            if status.checksum_drifted
            else None,
            "migrations",
        ),
        DoctorCheckResult(
            "migrations:pgmq",
            DoctorStatus.OK
            if status.pgmq_available
            and status.pgmq_installed
            and not status.missing_queues
            else DoctorStatus.FAIL,
            (
                "pgmq available, installed, and queues present"
                if status.pgmq_available
                and status.pgmq_installed
                and not status.missing_queues
                else "pgmq unavailable/absent or queues missing"
            ),
            "Install pgmq and run migrations to create queues."
            if not (
                status.pgmq_available
                and status.pgmq_installed
                and not status.missing_queues
            )
            else None,
            "migrations",
        ),
    ]
    return results


async def _check_migration_readiness(db_url: str | None) -> list[DoctorCheckResult]:
    """Inspect database migrations for readiness."""
    if not db_url:
        return [
            DoctorCheckResult(
                "migrations:database-url",
                DoctorStatus.FAIL,
                "NETENGINE_DB_URL/DATABASE_URL is not set",
                "Set NETENGINE_DB_URL or DATABASE_URL to a PostgreSQL connection string.",
                "migrations",
            )
        ]
    service = MigrationService(db_url, MIGRATIONS_DIR)
    try:
        status = await service.status()
    except Exception as exc:
        return [
            DoctorCheckResult(
                "migrations:status",
                DoctorStatus.FAIL,
                f"unable to inspect migrations: {exc}",
                "Verify database connectivity, credentials, and pgmq/Postgres availability.",
                "migrations",
            )
        ]
    return _migration_readiness_results(status)


def _feature_state_readiness_results(spec: Any) -> list[DoctorCheckResult]:
    """Return readiness warnings/failures for active feature-state fields."""
    results: list[DoctorCheckResult] = []
    for line in _feature_state_explanations(spec):
        status = (
            DoctorStatus.FAIL
            if ": unsupported " in line or ": reserved " in line
            else DoctorStatus.WARN
        )
        results.append(
            DoctorCheckResult(
                "feature-state",
                status,
                line,
                "Disable this field or use a NetEngine release that supports it."
                if status == DoctorStatus.FAIL
                else "Review alpha/experimental behavior before relying on it in production.",
                "spec",
                required=status == DoctorStatus.FAIL,
            )
        )
    if not results:
        results.append(
            DoctorCheckResult(
                "feature-state",
                DoctorStatus.OK,
                "no active unsupported or experimental feature-state fields",
                group="spec",
            )
        )
    return results


@click.group()
def cli() -> None:
    """NetEngine — spin up, reload, and tear down authority-autonomous worlds."""


cli.add_command(doctor)


@cli.group()
def migrate() -> None:
    """Manage NetEngine database migrations."""


@migrate.command("up")
def migrate_up() -> None:
    """Apply pending database migrations."""
    asyncio.run(_migrate_up())


async def _migrate_up() -> None:
    db_url = _require_db_url()
    service = MigrationService(db_url, MIGRATIONS_DIR)
    click.echo(f"Applying migrations from {MIGRATIONS_DIR}...")
    try:
        applied = await service.apply_pending()
    except Exception as exc:
        click.echo(f"Migration failed: {exc}", err=True)
        sys.exit(1)
    if applied:
        click.echo(f"Applied {len(applied)} migration(s):")
        for record in applied:
            click.echo(f"  ✓ {record.version} {record.name}")
    else:
        click.echo("No pending migrations.")


@migrate.command("status")
def migrate_status_service() -> None:
    """Print applied, pending, failed, and drifted migrations."""
    asyncio.run(_migrate_status(exit_on_unhealthy=False))


@migrate.command("check")
def migrate_check_service() -> None:
    """Validate migrations and pgmq prerequisites for CI."""
    asyncio.run(_migrate_status(exit_on_unhealthy=True))


async def _migrate_status(*, exit_on_unhealthy: bool) -> None:
    db_url = _require_db_url()
    service = MigrationService(db_url, MIGRATIONS_DIR)
    try:
        status = await service.status()
    except Exception as exc:
        click.echo(f"Unable to inspect migrations: {exc}", err=True)
        sys.exit(1)
    _print_migration_status(status)
    if exit_on_unhealthy:
        if status.ok:
            click.echo("Migration check passed.")
        else:
            click.echo("Migration check failed.", err=True)
            sys.exit(1)


@cli.command("readiness")
@click.argument("spec_file", type=click.Path(exists=True))
@click.option(
    "--db-url", default=db_url_from_env, help="PostgreSQL URL for migration checks."
)
@click.option(
    "--state-file",
    type=click.Path(path_type=Path),
    default=get_state_file,
    help="Runtime state file path for host preflight checks.",
)
@click.option(
    "--skip-db",
    is_flag=True,
    help="Skip doctor database probes; migration status is still checked.",
)
@click.option(
    "--env",
    "environment",
    help="Merge spec.{ENV}.yaml next to SPEC_FILE before validating.",
)
@click.option(
    "--set",
    "set_values",
    multiple=True,
    metavar="KEY=VALUE",
    help="Override a spec value; repeat for multiple dotted keys.",
)
def readiness(
    spec_file: str,
    db_url: str | None,
    state_file: Path,
    skip_db: bool,
    environment: str | None,
    set_values: tuple[str, ...],
) -> None:
    """Validate SPEC_FILE and report host, migration, and feature readiness."""
    asyncio.run(
        _readiness(spec_file, db_url, state_file, skip_db, environment, set_values)
    )


async def _readiness(
    spec_file: str,
    db_url: str | None,
    state_file: Path,
    skip_db: bool,
    environment: str | None = None,
    set_values: tuple[str, ...] = (),
) -> None:
    results: list[DoctorCheckResult] = []
    try:
        spec = _load_spec_for_cli(
            spec_file, environment=environment, set_values=set_values
        )
    except SpecLoadError as exc:
        _readiness_line(
            DoctorStatus.FAIL,
            "spec",
            f"validation failed: {exc}",
            "Fix the spec validation error and rerun readiness.",
        )
        sys.exit(1)

    results.append(
        DoctorCheckResult(
            "spec",
            DoctorStatus.OK,
            f"validated {spec.metadata.name}",
            group="spec",
        )
    )
    ctx = build_context(
        db_url,
        state_file,
        skip_db=skip_db,
        spec_subnets=tuple(
            str(network.subnet)
            for network in spec.substrate.networks.values()
            if getattr(network, "subnet", None)
        ),
    )
    results.extend(run_preflight(ctx))
    results.extend(await _check_migration_readiness(db_url))
    results.extend(_feature_state_readiness_results(spec))

    click.echo(f"NetEngine readiness summary for {spec.metadata.name}")
    counts = {
        status: sum(1 for result in results if result.status == status)
        for status in DoctorStatus
    }
    click.echo(
        "Summary: "
        f"{counts[DoctorStatus.OK]} pass, "
        f"{counts[DoctorStatus.WARN]} warn, "
        f"{counts[DoctorStatus.FAIL]} fail, "
        f"{counts[DoctorStatus.SKIP]} skip"
    )
    for result in results:
        _readiness_line(result.status, result.name, result.detail, result.hint)

    if any(
        result.status == DoctorStatus.FAIL and result.required for result in results
    ):
        sys.exit(1)


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option(
    "--explain",
    is_flag=True,
    default=False,
    help="Print active experimental, reserved, unsupported, or otherwise noteworthy fields.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Emit human-readable text or machine-readable JSON support-matrix results.",
)
@click.option(
    "--env",
    "environment",
    help="Merge spec.{ENV}.yaml next to SPEC_FILE before validating.",
)
@click.option(
    "--set",
    "set_values",
    multiple=True,
    metavar="KEY=VALUE",
    help="Override a spec value; repeat for multiple dotted keys.",
)
def validate(
    spec_file: str,
    explain: bool,
    output_format: str,
    environment: str | None,
    set_values: tuple[str, ...],
) -> None:
    """Validate SPEC_FILE without booting a world."""
    try:
        spec = _load_spec_for_cli(
            spec_file,
            environment=environment,
            set_values=set_values,
            validate_feature_states=False,
        )
    except SpecLoadError as exc:
        if output_format == "json":
            click.echo(
                json.dumps(
                    {"ok": False, "error": str(exc), "feature_states": []}, indent=2
                )
            )
        else:
            click.echo(f"Spec validation failed: {exc}", err=True)
        sys.exit(1)

    feature_states = _active_feature_state_results(spec)
    unsupported = [item for item in feature_states if item["state"] == "unsupported"]

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "ok": not unsupported,
                    "spec": spec.metadata.name,
                    "feature_states": feature_states,
                },
                indent=2,
            )
        )
    else:
        if unsupported:
            click.echo(
                "Spec validation failed: Unsupported spec features enabled:", err=True
            )
            for item in unsupported:
                click.echo(
                    f"  - {item['path']} is {item['state']} in {item['stage']}: {item['reason']}",
                    err=True,
                )
        else:
            click.echo(f"Spec validation succeeded: {spec.metadata.name}")
        if explain:
            explanations = _feature_state_explanations(spec)
            if explanations:
                click.echo("Feature-state details:")
                for line in explanations:
                    prefix = "WARNING: " if ": experimental " in line else ""
                    click.echo(f"  - {prefix}{line}")
            else:
                click.echo("Feature-state details: no active noteworthy fields.")

    if unsupported:
        sys.exit(1)


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option("--up-to", default=9, help="Stop after this phase number (0-9).")
@click.option(
    "--mock",
    is_flag=True,
    default=False,
    envvar="NETENGINE_MOCK",
    help="Run in mock mode (no real Docker/DNS calls).",
)
@click.option(
    "--skip-migrations",
    is_flag=True,
    default=False,
    help="Skip running database migrations on startup.",
)
@click.option(
    "--allow-migration-failure",
    is_flag=True,
    default=False,
    help="Continue booting if database migrations fail (development escape hatch).",
)
@click.option(
    "--env",
    "environment",
    help="Merge spec.{ENV}.yaml next to SPEC_FILE before booting.",
)
@click.option(
    "--set",
    "set_values",
    multiple=True,
    metavar="KEY=VALUE",
    help="Override a spec value; repeat for multiple dotted keys.",
)
def up(
    spec_file: str,
    up_to: int,
    mock: bool,
    skip_migrations: bool,
    allow_migration_failure: bool,
    environment: str | None,
    set_values: tuple[str, ...],
) -> None:
    """Boot a world from SPEC_FILE."""
    asyncio.run(
        _up(
            spec_file,
            up_to,
            mock,
            skip_migrations,
            allow_migration_failure,
            environment,
            set_values,
        )
    )


async def _up(
    spec_file: str,
    up_to: int,
    mock: bool,
    skip_migrations: bool,
    allow_migration_failure: bool = False,
    environment: str | None = None,
    set_values: tuple[str, ...] = (),
) -> None:
    spec = _load_spec_for_cli(spec_file, environment=environment, set_values=set_values)

    if mock:
        click.echo(
            "WARNING: running in mock mode — no real infrastructure will be created."
        )

    if not skip_migrations and not mock:
        db_url = _db_url_from_env()
        if db_url:
            try:
                await _run_migrations(db_url)
            except Exception as exc:
                if allow_migration_failure:
                    logger.warning(f"Migrations failed (continuing anyway): {exc}")
                else:
                    message = f"Migrations failed: {exc}"
                    logger.error(message)
                    click.echo(message, err=True)
                    sys.exit(1)

    orchestrator = Orchestrator(spec, mock_mode=mock)
    click.echo(f"Booting world from {spec_file} (phases 0–{up_to})…")

    import time

    _phase_start_times: dict[int, float] = {}

    def _on_start(phase_num: int, phase_name: str) -> None:
        _phase_start_times[phase_num] = time.monotonic()
        click.echo(f"  ⧗  Phase {phase_num}: {phase_name}…")

    def _on_complete(phase_num: int, phase_name: str) -> None:
        elapsed = time.monotonic() - _phase_start_times.get(phase_num, time.monotonic())
        click.echo(
            click.style(f"  ✓  Phase {phase_num}: {phase_name}", fg="green")
            + click.style(f"  ({elapsed:.1f}s)", fg="bright_black")
        )

    def _on_skip(phase_num: int, phase_name: str) -> None:
        click.echo(
            click.style(
                f"  –  Phase {phase_num}: {phase_name} (already done)",
                fg="bright_black",
            )
        )

    def _on_error(phase_num: int, phase_name: str, exc: Exception) -> None:
        click.echo(
            click.style(
                f"  ✗  Phase {phase_num}: {phase_name} — {exc}", fg="red", bold=True
            ),
            err=True,
        )

    try:
        await orchestrator.execute_phases(
            up_to_phase=up_to,
            on_phase_start=_on_start,
            on_phase_complete=_on_complete,
            on_phase_skip=_on_skip,
            on_phase_error=_on_error,
        )
    except Exception as exc:
        click.echo(f"Bootstrap failed: {exc}", err=True)
        sys.exit(1)

    click.echo("World bootstrapped.")
    _print_status(orchestrator.runtime_state)

    # Start background consumers if any were registered
    if orchestrator.consumer_supervisor.consumers:
        logger.info("Starting background consumers (Ctrl+C to stop).")
        await orchestrator.start_consumers()
        try:
            await asyncio.sleep(float("inf"))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await orchestrator.consumer_supervisor.stop_all()
            logger.info("Consumers stopped.")


@cli.group()
def migrate() -> None:
    """Inspect and apply database migrations."""


@migrate.command("run")
def migrate_run() -> None:
    """Apply pending database migrations."""
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        click.echo("No database URL configured for migrations", err=True)
        sys.exit(2)
    try:
        asyncio.run(_run_migrations(db_url))
    except Exception as exc:
        click.echo(f"Migrations failed: {exc}", err=True)
        sys.exit(1)


@migrate.command("status")
def migrate_status() -> None:
    """Show database migration status without applying migrations."""
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        click.echo("No database URL configured for migrations", err=True)
        sys.exit(2)
    try:
        report = asyncio.run(migration_status(db_url))
    except Exception as exc:
        click.echo(f"Migration status failed: {exc}", err=True)
        sys.exit(1)
    for migration in report.results:
        click.echo(f"{migration.status}: {migration.filename}")
    click.echo(
        f"Migrations status: {report.applied_count} applied, "
        f"{report.pending_count} pending, {report.failed_count} failed, "
        f"{report.drifted_count} drifted"
    )


@migrate.command("check")
def migrate_check() -> None:
    """Exit non-zero when migrations are pending, failed, or drifted."""
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        click.echo("No database URL configured for migrations", err=True)
        sys.exit(2)
    try:
        report = asyncio.run(migration_status(db_url))
    except Exception as exc:
        click.echo(f"Migration check failed: {exc}", err=True)
        sys.exit(1)
    if report.pending_count or report.failed_count or report.drifted_count:
        click.echo(
            f"Migrations not current: {report.pending_count} pending, "
            f"{report.failed_count} failed, {report.drifted_count} drifted"
        )
        sys.exit(1)
    click.echo("Migrations current.")


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
def reload(spec_file: str) -> None:
    """Diff SPEC_FILE against the running world and apply changes."""
    from netengine.core.reload import apply_reload
    from netengine.spec.models import NetEngineSpec

    state = RuntimeState.load()
    if not state.world_spec:
        click.echo("No running world found — use `netengine up` first.", err=True)
        sys.exit(1)

    new_spec = load_spec(spec_file)
    try:
        old_spec = NetEngineSpec(**state.world_spec)
    except Exception as exc:
        click.echo(f"Stored spec is corrupt: {exc}", err=True)
        sys.exit(1)

    is_ephemeral = old_spec.metadata.lifecycle.value == "ephemeral"
    click.echo("Computing diff…")
    result = asyncio.run(
        apply_reload(old_spec, new_spec, state, is_ephemeral=is_ephemeral)
    )

    if result.immutability_violations:
        click.echo("Reload REJECTED — immutable fields changed:", err=True)
        for v in result.immutability_violations:
            click.echo(f"  ✕ {v}", err=True)
        sys.exit(1)

    if result.applied:
        click.echo(f"Applied {len(result.applied)} change(s):")
        for entry in result.applied:
            click.echo(f"  ✓ {entry.detail}")
    else:
        click.echo("No changes to apply.")

    if result.errors:
        click.echo("Errors:", err=True)
        for e in result.errors:
            click.echo(f"  ! {e}", err=True)

    if not result.success:
        sys.exit(1)


@cli.command()
def status() -> None:
    """Show current world state and per-phase completion."""
    state = RuntimeState.load()
    _print_status(state)


_PGMQ_QUEUES = [q.value for q in Queue]

# Both prefixes are used by handlers: netengine_ (coredns, gateway) and netengines_ (all others)
_CONTAINER_PREFIXES = ("netengine_", "netengines_")


@cli.command()
@click.option(
    "--yes", is_flag=True, help="Skip confirmation prompt for ephemeral worlds."
)
@click.option(
    "--confirm",
    default=None,
    help="For persistent worlds, type the world name exactly.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be removed without removing it."
)
def down(yes: bool, confirm: str | None, dry_run: bool) -> None:
    """Tear down the running world (containers, networks, volumes)."""
    asyncio.run(_down(yes, confirm, dry_run))


async def _down(yes: bool, confirm: str | None, dry_run: bool) -> None:
    state = RuntimeState.load()
    if state.world_spec and not dry_run:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get(
            "lifecycle", "ephemeral"
        )
        world_name = (state.world_spec.get("metadata") or {}).get("name", "")
        if raw_lifecycle == "persistent" and confirm != world_name:
            raise click.ClickException(
                "Persistent world teardown requires typed confirmation. "
                f"Re-run with --confirm {world_name!r}."
            )
        if raw_lifecycle != "persistent" and not yes:
            click.confirm(
                "This world will be destroyed. Continue?",
                abort=True,
            )

    if dry_run:
        click.echo("Dry run — nothing will be removed.\n")

    removed: list[str] = []
    errors: list[str] = []

    # --- Docker: containers, networks, volumes ---
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()

        # Collect container IDs from state for precise targeting (avoids prefix-only scan misses)
        state_container_ids: set[str] = set(
            filter(
                None,
                [
                    state.dns_root_container_id,
                    state.gateway_container_id,
                    state.step_ca_container_id,
                    state.keycloak_platform_container_id,
                    state.inworld_keycloak_container_id,
                ],
            )
        )

        for container in client.containers.list(all=True):
            by_id = container.id in state_container_ids
            by_prefix = container.name and any(
                container.name.startswith(p) for p in _CONTAINER_PREFIXES
            )
            if by_id or by_prefix:
                label = f"container:{container.name}"
                if dry_run:
                    click.echo(f"  would remove  {label}")
                    removed.append(label)
                else:
                    try:
                        container.stop(timeout=5)
                        container.remove(force=True)
                        removed.append(label)
                    except Exception as exc:
                        errors.append(f"{label}: {exc}")

        for network in client.networks.list():
            if network.name and any(
                network.name.startswith(p) for p in _CONTAINER_PREFIXES
            ):
                label = f"network:{network.name}"
                if dry_run:
                    click.echo(f"  would remove  {label}")
                    removed.append(label)
                else:
                    try:
                        network.remove()
                        removed.append(label)
                    except Exception as exc:
                        errors.append(f"{label}: {exc}")

        for volume in client.volumes.list():
            if any(volume.name.startswith(p) for p in _CONTAINER_PREFIXES):
                label = f"volume:{volume.name}"
                if dry_run:
                    click.echo(f"  would remove  {label}")
                    removed.append(label)
                else:
                    try:
                        volume.remove(force=True)
                        removed.append(label)
                    except Exception as exc:
                        errors.append(f"{label}: {exc}")

    except Exception as exc:
        errors.append(f"Docker unavailable: {exc}")

    # --- Zone files ---
    import shutil

    from netengine.handlers.context import default_zone_dir

    zone_dir = Path(os.environ.get("NETENGINE_ZONE_DIR", default_zone_dir()))
    if zone_dir.exists():
        label = f"zone-files:{zone_dir}"
        if dry_run:
            click.echo(f"  would remove  {label}")
            removed.append(label)
        else:
            try:
                shutil.rmtree(zone_dir)
                removed.append(label)
            except Exception as exc:
                errors.append(f"{label}: {exc}")

    # --- pgmq queues ---
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if db_url:
        for queue in _PGMQ_QUEUES:
            label = f"queue:{queue}"
            if dry_run:
                click.echo(f"  would purge   {label}")
                removed.append(label)
            else:
                try:
                    import asyncpg  # type: ignore[import]

                    conn = await asyncpg.connect(db_url)
                    try:
                        await conn.execute("SELECT pgmq.purge_queue($1)", queue)
                        removed.append(label)
                    except Exception:
                        pass  # queue may not exist yet — non-fatal
                    finally:
                        await conn.close()
                except Exception as exc:
                    errors.append(f"{label}: {exc}")

    # --- Runtime state file ---
    from netengine.core.state import get_state_file

    state_file = get_state_file()
    if state_file.exists():
        label = f"state:{state_file.name}"
        if dry_run:
            click.echo(f"  would remove  {label}")
            removed.append(label)
        else:
            state_file.unlink()
            removed.append(label)

    # --- Summary ---
    if dry_run:
        click.echo(f"\n{len(removed)} resource(s) would be removed.")
        return

    if removed:
        click.echo(f"Removed {len(removed)} resource(s):")
        for r in removed:
            click.echo(f"  ✓ {r}")
    else:
        click.echo("Nothing to remove.")

    if errors:
        click.echo("Errors:", err=True)
        for e in errors:
            click.echo(f"  ! {e}", err=True)
        sys.exit(1)
    else:
        click.echo("World destroyed.")


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON.")
def diagnose(spec_file: str, as_json: bool) -> None:
    """Probe all running world components and report health."""
    asyncio.run(_diagnose(spec_file, as_json))


async def _diagnose(spec_file: str, as_json: bool) -> None:
    from netengine.diagnostic.runner import ProbeStatus, build_runner

    spec = load_spec(spec_file)
    runner = build_runner(spec)
    results = await runner.run()

    if as_json:
        import json as _json

        payload = [
            {
                "name": r.name,
                "status": r.status.value,
                "detail": r.detail,
                "hint": r.hint,
                "remediation": r.remediation,
                "related_phase": r.related_phase,
                "related_resource": r.related_resource,
                "related_logs": r.related_logs,
                "command_to_retry": r.command_to_retry,
                "elapsed_ms": round(r.elapsed_ms, 1)
                if r.elapsed_ms is not None
                else None,
            }
            for r in results
        ]
        click.echo(_json.dumps(payload, indent=2))
        issues = sum(
            1 for r in results if r.status in (ProbeStatus.FAIL, ProbeStatus.WARN)
        )
        if issues:
            sys.exit(1)
        return

    world_name = spec.metadata.name
    total = len(results)
    click.echo(f"\nWorld: {world_name}  [{total} checks]\n")

    _STATUS_SYMBOL = {
        ProbeStatus.OK: click.style("  ✓", fg="green"),
        ProbeStatus.WARN: click.style("  !", fg="yellow"),
        ProbeStatus.FAIL: click.style("  ✗", fg="red", bold=True),
        ProbeStatus.SKIP: click.style("  –", fg="bright_black"),
    }

    for r in results:
        symbol = _STATUS_SYMBOL[r.status]
        timing = f"  ({r.elapsed_ms:.0f}ms)" if r.elapsed_ms is not None else ""
        phase = f"Phase {r.related_phase} " if r.related_phase is not None else ""
        resource = f": {r.related_resource}" if r.related_resource else ""
        click.echo(f"{symbol}  {phase}{r.name}{resource} — {r.status.value}{timing}")
        click.echo(f"{'':14}  Cause: {r.detail}")
        actionable = r.status in (ProbeStatus.FAIL, ProbeStatus.WARN)
        if r.remediation and actionable:
            click.echo(f"{'':14}  Remediation: {r.remediation}")
        if r.related_logs and actionable:
            for log_command in r.related_logs:
                click.echo(f"{'':14}  Try: {log_command}")
        if r.command_to_retry and actionable:
            click.echo(f"{'':14}  Try: {r.command_to_retry}")

    issues = [r for r in results if r.status in (ProbeStatus.FAIL, ProbeStatus.WARN)]
    skipped = [r for r in results if r.status == ProbeStatus.SKIP]

    click.echo("")
    if not issues:
        click.echo(click.style("All checks passed.", fg="green"))
    else:
        issue_word = "issue" if len(issues) == 1 else "issues"
        click.echo(click.style(f"{len(issues)} {issue_word} found.", fg="red"))
    if skipped:
        click.echo(f"{len(skipped)} check(s) skipped (disabled in spec).")

    if issues:
        sys.exit(1)


@cli.command()
@click.option(
    "--interval", default=30, type=int, help="Poll interval in seconds (default 30)."
)
@click.option(
    "--max-retries", default=3, type=int, help="Max self-heal retries per phase."
)
@click.option("--no-auto-heal", is_flag=True, help="Detect drift but don't auto-heal.")
def drift_watch(interval: int, max_retries: int, no_auto_heal: bool) -> None:
    """Watch running world for drift and optionally auto-heal (Ctrl+C to stop)."""
    asyncio.run(_drift_watch(interval, max_retries, no_auto_heal))


async def _drift_watch(interval: int, max_retries: int, no_auto_heal: bool) -> None:
    state = RuntimeState.load()
    if not state.world_spec:
        click.echo("No running world found — use `netengine up` first.", err=True)
        sys.exit(1)

    click.echo(
        f"Starting drift detection (interval={interval}s, auto-heal={not no_auto_heal})…"
    )
    click.echo("Press Ctrl+C to stop.\n")

    orchestrator = Orchestrator(state.world_spec, mock_mode=False)

    orchestrator.start_drift_detection(
        poll_interval_seconds=interval,
        max_drift_retries=max_retries,
        auto_heal=not no_auto_heal,
    )

    try:
        await orchestrator.start_consumers()
        try:
            await asyncio.sleep(float("inf"))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await orchestrator.consumer_supervisor.stop_all()
            click.echo("\nDrift detection stopped.")
    except Exception as exc:
        click.echo(f"Drift detection error: {exc}", err=True)
        sys.exit(1)


@cli.command()
def drift_status() -> None:
    """Show current drift status and history."""
    state = RuntimeState.load()

    if not state.world_spec:
        click.echo("No running world found — use `netengine up` first.", err=True)
        sys.exit(1)

    click.echo("\nDrift Status\n")

    if state.last_drift_check_at:
        check_time = (
            state.last_drift_check_at.isoformat()
            if hasattr(state.last_drift_check_at, "isoformat")
            else str(state.last_drift_check_at)
        )
        click.echo(f"Last check: {check_time}")
    else:
        click.echo("Last check: (no checks yet)")

    if state.current_drift_phases:
        click.echo(
            f"\nCurrently drifted phases: {', '.join(str(p) for p in state.current_drift_phases)}"
        )
    else:
        click.echo("\nCurrently drifted phases: none")

    if state.drift_history:
        click.echo("\nRecent drift history (last 10 events):")
        for event in state.drift_history[-10:]:
            phase = event.get("phase_num", "?")
            detected = event.get("detected_at", "?")
            healed = event.get("healed_at")
            failed = event.get("healing_failed", False)

            if healed:
                status = f"✓ healed at {healed}"
            elif failed:
                error = event.get("error", "unknown error")
                status = f"✗ healing failed: {error}"
            else:
                status = "⧗ healing in progress"

            click.echo(f"  Phase {phase}: detected at {detected}, {status}")
    else:
        click.echo("\nDrift history: (no events)")


@cli.command("export")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False),
    default="netengine-support-bundle.json",
    show_default=True,
)
def export_support_bundle(out_path: str) -> None:
    """Write a sanitized support bundle for backup/support/import."""
    import datetime as _dt
    import json as _json

    from netengine.api.routes import (
        IMPORT_SCHEMA_VERSION,
        SUPPORT_BUNDLE_SCHEMA_VERSION,
        _sanitize_export_value,
    )
    from netengine.core.state import RUNTIME_STATE_SCHEMA_VERSION, RuntimeState
    from netengine.spec.models import SPEC_SCHEMA_VERSION

    state = RuntimeState.load()
    bundle = {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "support_bundle_schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
        "bundle_kind": "netengine.support",
        "exported_at": _dt.datetime.utcnow().isoformat(),
        "runtime_state_schema_version": state.schema_version,
        "supported_runtime_state_schema_version": RUNTIME_STATE_SCHEMA_VERSION,
        "spec_schema_version": ((state.world_spec or {}).get("metadata") or {}).get(
            "schema_version", SPEC_SCHEMA_VERSION
        ),
        "spec": state.world_spec,
        "phase_completed": state.phase_completed,
        "ca_cert_pem": state.ca_cert_pem,
        "substrate_output": _sanitize_export_value(state.substrate_output),
        "pki_output": _sanitize_export_value(state.pki_output),
        "dns_output": _sanitize_export_value(state.dns_output),
        "identity_platform_output": _sanitize_export_value(
            state.identity_platform_output
        ),
        "world_registry_output": _sanitize_export_value(state.world_registry_output),
        "domain_registry_output": _sanitize_export_value(state.domain_registry_output),
        "identity_inworld_output": _sanitize_export_value(
            state.identity_inworld_output
        ),
        "ands_output": _sanitize_export_value(state.ands_output),
        "world_services_output": _sanitize_export_value(state.world_services_output),
        "org_apps_output": _sanitize_export_value(state.org_apps_output),
    }
    path = Path(out_path)
    path.write_text(_json.dumps(bundle, indent=2) + "\n")
    os.chmod(path, 0o600)
    click.echo(f"Wrote support bundle: {path}")


@cli.command("import")
@click.argument("bundle_file", type=click.Path(exists=True, dir_okay=False))
def import_support_bundle(bundle_file: str) -> None:
    """Validate and restore a compatible support bundle."""
    import json as _json

    from netengine.api.routes import (
        SUPPORTED_IMPORT_SCHEMA_VERSIONS,
        _validate_import_phase_state,
    )
    from netengine.core.state import RuntimeState
    from netengine.spec.models import SUPPORTED_SPEC_SCHEMA_VERSIONS, NetEngineSpec

    body = _json.loads(Path(bundle_file).read_text())
    if body.get("schema_version") not in SUPPORTED_IMPORT_SCHEMA_VERSIONS:
        raise click.ClickException(
            f"Unsupported bundle schema_version: {body.get('schema_version')!r}"
        )
    spec_data = body.get("spec")
    if not isinstance(spec_data, dict):
        raise click.ClickException("Support bundle is missing a spec object")
    spec_schema = (spec_data.get("metadata") or {}).get("schema_version")
    if spec_schema is not None and spec_schema not in SUPPORTED_SPEC_SCHEMA_VERSIONS:
        raise click.ClickException(
            f"Unsupported spec metadata.schema_version: {spec_schema!r}"
        )
    spec = NetEngineSpec.model_validate(spec_data)
    imported_state = RuntimeState(
        world_spec=spec.model_dump(mode="json"),
        phase_completed=dict(body.get("phase_completed") or {}),
        ca_cert_pem=body.get("ca_cert_pem"),
        substrate_output=body.get("substrate_output"),
        pki_output=body.get("pki_output"),
        dns_output=body.get("dns_output"),
        identity_platform_output=body.get("identity_platform_output"),
        world_registry_output=body.get("world_registry_output"),
        domain_registry_output=body.get("domain_registry_output"),
        identity_inworld_output=body.get("identity_inworld_output"),
        ands_output=body.get("ands_output"),
        world_services_output=body.get("world_services_output"),
        org_apps_output=body.get("org_apps_output"),
        pki_bootstrapped=bool(body.get("ca_cert_pem") or body.get("pki_output")),
    )
    _validate_import_phase_state(imported_state)
    imported_state.save()
    click.echo(f"Imported support bundle for world: {spec.metadata.name}")


@cli.command()
@click.option(
    "--queue",
    default=None,
    type=click.Choice([q.value for q in PRIMARY_QUEUES]),
    help="Show depth for a specific queue (default: all).",
)
@click.option("--dlq", is_flag=True, help="Show dead-letter queue contents.")
@click.option("--limit", default=10, show_default=True, help="Max messages to display.")
def events(queue: str | None, dlq: bool, limit: int) -> None:
    """Inspect event queue depths and dead-letter queue contents."""
    asyncio.run(_events(queue, dlq, limit))


async def _events(queue: str | None, dlq: bool, limit: int) -> None:
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        click.echo(
            "NETENGINE_DB_URL is not set — event inspection requires a direct DB connection.",
            err=True,
        )
        sys.exit(1)

    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        click.echo("asyncpg is not installed.", err=True)
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        queues_to_check = [queue] if queue else [q.value for q in PRIMARY_QUEUES]

        if dlq:
            click.echo("\nDead-letter queue contents:\n")
            for q in queues_to_check:
                dlq_name = dlq_for(Queue(q)).value
                try:
                    rows = await conn.fetch(
                        "SELECT msg_id, message, enqueued_at, read_ct "
                        "FROM pgmq.q_$1 ORDER BY enqueued_at DESC LIMIT $2",
                        dlq_name,
                        limit,
                    )
                    if rows:
                        click.echo(
                            click.style(
                                f"  {dlq_name} ({len(rows)} message(s)):", bold=True
                            )
                        )
                        for row in rows:
                            import json as _json

                            try:
                                payload = _json.loads(row["message"])
                                event_type = payload.get("event_type", "?")
                                emitted_by = payload.get("emitted_by", "?")
                                retry_count = payload.get("retry_count", 0)
                                dlq_reason = (payload.get("payload") or {}).get(
                                    "dlq_reason", ""
                                )
                                click.echo(
                                    f"    [{row['msg_id']}] {event_type} "
                                    f"from={emitted_by} retries={retry_count}"
                                    + (f" reason={dlq_reason}" if dlq_reason else "")
                                )
                            except Exception:
                                click.echo(
                                    f"    [{row['msg_id']}] (unparseable message)"
                                )
                    else:
                        click.echo(f"  {dlq_name}: empty")
                except Exception as exc:
                    click.echo(f"  {dlq_name}: error reading — {exc}")
        else:
            click.echo("\nEvent queue depths:\n")
            for q in queues_to_check:
                dlq_name = dlq_for(Queue(q)).value
                try:
                    depth_row = await conn.fetchrow(
                        "SELECT count(*) AS depth FROM pgmq.q_$1", q
                    )
                    dlq_row = await conn.fetchrow(
                        "SELECT count(*) AS depth FROM pgmq.q_$1", dlq_name
                    )
                    depth = depth_row["depth"] if depth_row else 0
                    dlq_depth = dlq_row["depth"] if dlq_row else 0
                    status = (
                        click.style("✓", fg="green")
                        if depth == 0
                        else click.style("!", fg="yellow")
                    )
                    dlq_status = (
                        ""
                        if dlq_depth == 0
                        else click.style(f"  DLQ: {dlq_depth}", fg="red")
                    )
                    click.echo(f"  {status}  {q:<30} depth={depth}{dlq_status}")
                except Exception as exc:
                    click.echo(f"  ?  {q}: error — {exc}")
    finally:
        await conn.close()


def _print_status(state: RuntimeState) -> None:
    world_name = None
    if state.world_spec and isinstance(state.world_spec, dict):
        world_name = (state.world_spec.get("metadata") or {}).get("name")

    if world_name:
        click.echo(f"\nWorld: {world_name}")
    else:
        click.echo("\nNo world spec stored.")

    click.echo("Phase status:")
    for phase_id, label in PHASE_LABELS.items():
        completed = state.phase_completed.get(phase_id, False)
        marker = "✓" if completed else "·"
        click.echo(f"  {marker}  Phase {phase_id}: {label}")

    if state.last_error:
        click.echo(f"\nLast error: {state.last_error}")
    if state.ca_cert_pem:
        click.echo("CA certificate: present")
    if state.step_ca_container_id:
        click.echo(f"step-ca container: {state.step_ca_container_id}")

    failures = state.event_send_failures
    if failures:
        from netengine.events.emitter import _PGMQ_DISABLED_QUEUE

        disabled_drops = [f for f in failures if f.get("queue") == _PGMQ_DISABLED_QUEUE]
        send_failures = [f for f in failures if f.get("queue") != _PGMQ_DISABLED_QUEUE]
        click.echo("")
        if disabled_drops:
            click.echo(
                f"WARNING: pgmq client not wired — {len(disabled_drops)} event(s) silently "
                "dropped in last run. Run `netengine doctor` for details."
            )
        if send_failures:
            click.echo(f"Event send failures: {len(send_failures)}")
            for failure in send_failures[-3:]:
                click.echo(
                    f"  ! [{failure.get('queue', '?')}] {failure.get('event_type', '?')}: "
                    f"{failure.get('exception', '?')}"
                )


@cli.command()
@click.option("--name", default=None, help="World name (pre-fills wizard prompt).")
@click.option(
    "--lifecycle",
    type=click.Choice(["ephemeral", "persistent"]),
    default=None,
    help="World lifecycle mode (pre-fills wizard prompt).",
)
@click.option(
    "--preset",
    type=click.Choice(["minimal", "single-org", "dev-sandbox"]),
    default=None,
    help=(
        "Skip sections of the wizard with a preset. "
        "minimal: no orgs, services off. "
        "single-org: one org with services and Gitea. "
        "dev-sandbox: two orgs, all services, dev apps."
    ),
)
@click.option(
    "--output", "-o", default=None, help="Output file path (default: <name>.yaml)."
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Accept all defaults without prompting (useful for CI/scripts).",
)
def init(
    name: str | None,
    lifecycle: str | None,
    preset: str | None,
    output: str | None,
    yes: bool,
) -> None:
    """Interactively scaffold a new world spec — DNS, PKI, orgs, services, and apps.

    \b
    Preset modes (--preset):
      minimal     Bare-bones spec — no orgs, services off
      single-org  One org with mail, storage, and Gitea
      dev-sandbox Two orgs, all services, Gitea + Mailpit

    \b
    Without a preset the full wizard runs, covering:
      • World identity and lifecycle
      • Network subnets and internet isolation mode
      • Certificate authority details (CN, org, country, lifetime, CRL/OCSP)
      • Platform administrator account
      • Organisations with AND profiles, capabilities, and users
      • Extra TLDs
      • Mail (Postfix) and storage (MinIO) services
      • Org app catalog (Gitea, Mailpit)

    The generated spec is validated against the Pydantic models before writing.
    """
    from netengine.cli.init_wizard import WorldConfig, build_spec_yaml, run_wizard
    from netengine.spec.loader import load_spec

    # When --output is explicit we know the path before the wizard runs — check early
    # so the user isn't asked to fill in the whole wizard only to have it abort.
    if output and not yes:
        early_path = Path(output)
        if early_path.exists():
            click.confirm(f"{early_path} already exists — overwrite?", abort=True)

    try:
        cfg: WorldConfig = run_wizard(
            preset=preset, yes=yes, name=name, lifecycle=lifecycle
        )
    except click.Abort:
        click.echo("\nAborted.", err=True)
        return

    out_path = Path(output) if output else Path(f"{cfg.name}.yaml")

    # When --output was not set, we now know the name-derived path — check it here.
    if not output and out_path.exists() and not yes:
        click.confirm(f"\n{out_path} already exists — overwrite?", abort=True)

    spec_yaml = build_spec_yaml(cfg)

    # Validate before writing
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write(spec_yaml)
        tmp_path = tmp.name

    try:
        load_spec(tmp_path)
    except Exception as exc:
        import os as _os

        _os.unlink(tmp_path)
        click.echo(
            f"\nSpec validation failed — please report this as a bug:\n  {exc}",
            err=True,
        )
        raise SystemExit(1) from exc

    import os as _os

    _os.unlink(tmp_path)
    out_path.write_text(spec_yaml)

    _print_init_summary(cfg, out_path)


def _print_init_summary(cfg: "Any", out_path: Path) -> None:
    click.echo(
        f"\n{click.style('✓', fg='green')} Created {click.style(str(out_path), bold=True)}\n"
    )

    # What was configured
    click.echo(click.style("World summary:", fg="cyan"))
    click.echo(f"  Name:       {cfg.name}")
    click.echo(f"  Lifecycle:  {cfg.lifecycle}")
    if cfg.environment:
        click.echo(f"  Env:        {cfg.environment}")
    click.echo(f"  Subnets:    platform={cfg.platform_subnet}  core={cfg.core_subnet}")
    click.echo(f"  Internet:   {cfg.internet_mode}")

    if cfg.orgs:
        click.echo(f"\n  Organisations ({len(cfg.orgs)}):")
        for org in cfg.orgs:
            user_count = len(org.users)
            click.echo(
                f"    • {org.name:<20} profile={org.and_profile}  users={user_count}"
            )
    else:
        click.echo("\n  Organisations: none (add later with `netengine reload`)")

    services = []
    if cfg.mail_enabled:
        services.append(f"mail (quota={cfg.mail_quota_mb}MB, DMARC={cfg.dmarc_policy})")
    if cfg.storage_enabled:
        services.append(f"storage ({', '.join(cfg.storage_buckets)})")
    if services:
        click.echo(f"\n  Services: {', '.join(services)}")
    else:
        click.echo("\n  Services: none")

    apps = []
    if cfg.gitea_enabled:
        apps.append("gitea")
    if cfg.mailpit_enabled:
        apps.append("mailpit")
    if apps:
        click.echo(f"  Apps:     {', '.join(apps)}")

    click.echo(click.style("\nNext steps:", fg="cyan"))
    click.echo("\n  1. Start local Postgres + pgmq:")
    click.echo("       docker compose up -d db\n")
    click.echo("  2. Boot your world:")
    click.echo(f"       netengine up {out_path}\n")
    click.echo("  3. Check phase status:")
    click.echo("       netengine status\n")
    click.echo("  4. Diagnose running services:")
    click.echo(f"       netengine diagnose {out_path}\n")
    click.echo("  5. Tear down when done:")
    click.echo("       netengine down\n")
    click.echo(
        f"Edit {out_path} directly or use `netengine reload {out_path}` to apply changes live."
    )


if __name__ == "__main__":
    cli()
