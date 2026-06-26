"""NetEngine CLI — operator surface for world management."""

import asyncio
import logging
import os
import sys
from pathlib import Path

import click

from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.spec.loader import load_spec

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

PHASE_LABELS = {
    "0": "Substrate",
    "1": "DNS root + platform zones",
    "2": "DNS TLD hierarchy",
    "3": "PKI + ACME",
    "4": "Platform identity",
    "5": "Registries",
    "6": "In-world identity",
    "7": "ANDs",
    "8": "Services",
}
MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


async def _run_migrations(db_url: str) -> None:
    """Run all SQL migration files in order against the given Postgres URL."""
    import asyncpg  # type: ignore[import]

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        logger.info("No migration files found")
        return

    conn = await asyncpg.connect(db_url)
    try:
        for migration_path in migration_files:
            sql = migration_path.read_text()
            logger.info(f"Running migration: {migration_path.name}")
            await conn.execute(sql)
        logger.info(f"Applied {len(migration_files)} migration(s)")
    finally:
        await conn.close()


@click.group()
def cli() -> None:
    """NetEngine — spin up, reload, and tear down authority-autonomous worlds."""


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option("--up-to", default=8, help="Stop after this phase number (0-8).")
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
def up(spec_file: str, up_to: int, mock: bool, skip_migrations: bool) -> None:
    """Boot a world from SPEC_FILE."""
    asyncio.run(_up(spec_file, up_to, mock, skip_migrations))


async def _up(spec_file: str, up_to: int, mock: bool, skip_migrations: bool) -> None:
    spec = load_spec(spec_file)

    if mock:
        click.echo("WARNING: running in mock mode — no real infrastructure will be created.")

    if not skip_migrations and not mock:
        db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
        if db_url:
            try:
                await _run_migrations(db_url)
            except Exception as exc:
                logger.warning(f"Migrations failed (continuing anyway): {exc}")

    orchestrator = Orchestrator(spec, mock_mode=mock)
    click.echo(f"Booting world from {spec_file} (phases 0–{up_to})…")
    try:
        await orchestrator.execute_phases(up_to_phase=up_to)
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
    result = asyncio.run(apply_reload(old_spec, new_spec, state, is_ephemeral=is_ephemeral))

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


_PGMQ_QUEUES = [
    "dns_updates",
    "dns_updates_dlq",
    "oidc_provisioning",
    "oidc_provisioning_dlq",
    "and_provisioning",
    "and_provisioning_dlq",
    "world_health",
    "world_health_dlq",
]

# Both prefixes are used by handlers: netengine_ (coredns, gateway) and netengines_ (all others)
_CONTAINER_PREFIXES = ("netengine_", "netengines_")


@cli.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without removing it.")
def down(yes: bool, dry_run: bool) -> None:
    """Tear down the running world (containers, networks, volumes)."""
    asyncio.run(_down(yes, dry_run))


async def _down(yes: bool, dry_run: bool) -> None:
    state = RuntimeState.load()
    if state.world_spec and not dry_run:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get("lifecycle", "ephemeral")
        if raw_lifecycle == "persistent" and not yes:
            click.confirm(
                "This is a PERSISTENT world — all durable state will be destroyed. Continue?",
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
            by_prefix = any(container.name.startswith(p) for p in _CONTAINER_PREFIXES)
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
            if any(network.name.startswith(p) for p in _CONTAINER_PREFIXES):
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

    from netengine.handlers.context import DEFAULT_ZONE_DIR

    zone_dir = Path(os.environ.get("NETENGINE_ZONE_DIR", DEFAULT_ZONE_DIR))
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
                "elapsed_ms": round(r.elapsed_ms, 1) if r.elapsed_ms is not None else None,
            }
            for r in results
        ]
        click.echo(_json.dumps(payload, indent=2))
        issues = sum(1 for r in results if r.status in (ProbeStatus.FAIL, ProbeStatus.WARN))
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
        click.echo(f"{symbol}  {r.name:<10} {r.detail}{timing}")
        if r.hint and r.status != ProbeStatus.OK:
            click.echo(f"{'':14}  {click.style('→', fg='cyan')} {r.hint}")

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


if __name__ == "__main__":
    cli()
