# netengine/cli/main.py
import asyncio
import logging
import os
from pathlib import Path

import click

from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.spec.loader import load_spec

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
def cli():
    pass


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
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
def up(spec_file: str, mock: bool, skip_migrations: bool) -> None:
    """Boot a world from the given spec YAML."""
    asyncio.run(_up(spec_file, mock, skip_migrations))


async def _up(spec_file: str, mock: bool, skip_migrations: bool) -> None:
    spec = load_spec(spec_file)

    # Run migrations if a local Postgres URL is configured
    if not skip_migrations and not mock:
        db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
        if db_url:
            try:
                await _run_migrations(db_url)
            except Exception as exc:
                logger.warning(f"Migrations failed (continuing anyway): {exc}")
        else:
            logger.debug("No NETENGINE_DB_URL set — skipping migrations")

    orchestrator = Orchestrator(spec, mock_mode=mock)
    await orchestrator.execute_phases()


@cli.command()
def status() -> None:
    """Show current world state."""
    state = RuntimeState.load()
    phase_labels = {
        "0": "Substrate",
        "1": "DNS root/platform zones",
        "2": "DNS TLD setup",
        "3": "PKI",
        "4": "Platform identity",
        "5": "Registries",
        "6": "In-world identity",
        "7": "ANDs",
        "8": "Services",
    }
    click.echo("Phases completed:")
    for phase, label in phase_labels.items():
        completed = state.phase_completed.get(phase, False)
        marker = "✓" if completed else "·"
        click.echo(f"  {marker} Phase {phase}: {label}")
    click.echo(f"CA certificate present: {bool(state.ca_cert_pem)}")
    click.echo(f"step-ca container ID: {state.step_ca_container_id}")
    if state.last_error:
        click.echo(f"Last error: {state.last_error}")


@cli.command()
def down() -> None:
    """Tear down the world (kill containers, remove volumes)."""
    click.echo("Teardown not yet implemented.")


if __name__ == "__main__":
    cli()
