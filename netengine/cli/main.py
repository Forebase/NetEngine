# netengine/cli/main.py
import asyncio
import logging
import click
from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.spec.loader import load_spec

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    pass


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
def up(spec_file):
    """Boot a world from the given spec YAML."""
    spec = load_spec(spec_file)
    orchestrator = Orchestrator(spec)
    asyncio.run(orchestrator.execute_phases())


@cli.command()
def status():
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
    click.echo(f"step‑ca container ID: {state.step_ca_container_id}")


@cli.command()
def down():
    """Tear down the world (kill containers, remove volumes)."""
    # Not fully implemented for M2 – will be done in M8.
    click.echo("Teardown not yet implemented.")


if __name__ == "__main__":
    cli()
