# netengines/cli/main.py
import asyncio
import logging
from pathlib import Path

import click
import yaml

from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    pass


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
def up(spec_file):
    """Boot a world from the given spec YAML."""
    with open(spec_file, "r") as f:
        spec = yaml.safe_load(f)
    orchestrator = Orchestrator(spec)
    asyncio.run(orchestrator.run())


@cli.command()
def status():
    """Show current world state."""
    state = RuntimeState.load()
    click.echo(f"Phases completed: {state.phase_completed}")
    click.echo(f"CA certificate present: {bool(state.ca_cert_pem)}")
    click.echo(f"step‑ca container ID: {state.step_ca_container_id}")


@cli.command()
def down():
    """Tear down the world (kill containers, remove volumes)."""
    # Not fully implemented for M2 – will be done in M8.
    click.echo("Teardown not yet implemented.")


if __name__ == "__main__":
    cli()
