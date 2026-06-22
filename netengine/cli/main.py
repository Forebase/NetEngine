"""NetEngine CLI — operator surface for world management."""

import asyncio
import logging
import sys

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


@click.group()
def cli() -> None:
    """NetEngine — spin up, reload, and tear down authority-autonomous worlds."""


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option("--up-to", default=8, help="Stop after this phase number (0-8).")
def up(spec_file: str, up_to: int) -> None:
    """Boot a world from SPEC_FILE."""
    spec = load_spec(spec_file)
    orchestrator = Orchestrator(spec)
    click.echo(f"Booting world from {spec_file} (phases 0–{up_to})…")
    try:
        asyncio.run(orchestrator.execute_phases(up_to_phase=up_to))
        click.echo("World bootstrapped.")
        _print_status(orchestrator.runtime_state)
    except Exception as exc:
        click.echo(f"Bootstrap failed: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("spec_file", type=click.Path(exists=True))
def reload(spec_file: str) -> None:
    """Diff SPEC_FILE against the running world and apply changes."""
    from netengine.core.reload import apply_reload
    from netengine.spec.models import NetEngineSpec

    state = RuntimeState.load()
    if not state.world_spec:
        click.echo("No running world found — use `netengines up` first.", err=True)
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


@cli.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def down(yes: bool) -> None:
    """Tear down the running world (containers, networks, volumes)."""
    state = RuntimeState.load()
    if state.world_spec:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get("lifecycle", "ephemeral")
        if raw_lifecycle == "persistent" and not yes:
            click.confirm(
                "This is a PERSISTENT world — all durable state will be destroyed. Continue?",
                abort=True,
            )

    removed: list[str] = []
    errors: list[str] = []

    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()

        for container in client.containers.list(all=True):
            if container.name.startswith("netengines_"):
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                    removed.append(container.name)
                except Exception as exc:
                    errors.append(f"container {container.name}: {exc}")

        for network in client.networks.list():
            if network.name.startswith("netengines_"):
                try:
                    network.remove()
                    removed.append(f"network:{network.name}")
                except Exception as exc:
                    errors.append(f"network {network.name}: {exc}")

        for volume in client.volumes.list():
            if volume.name.startswith("netengines_"):
                try:
                    volume.remove(force=True)
                    removed.append(f"volume:{volume.name}")
                except Exception as exc:
                    errors.append(f"volume {volume.name}: {exc}")

    except Exception as exc:
        errors.append(f"Docker unavailable: {exc}")

    # Clear local state file
    from netengine.core.state import get_state_file
    state_file = get_state_file()
    if state_file.exists():
        state_file.unlink()
        removed.append("state:netengines_state.json")

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
