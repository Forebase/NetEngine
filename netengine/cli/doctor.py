"""CLI wrapper for NetEngine host-readiness checks.

``netengine doctor`` validates local prerequisites before a spec is loaded or a
world is booted. Runtime diagnostics for an already configured/running world
live in :mod:`netengine.diagnostic.runner` and require ``NetEngineSpec``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import click

from netengine.cli.env import db_url_from_env
from netengine.core.state import get_state_file
from netengine.diagnostic import preflight as _preflight
from netengine.diagnostic.preflight import (
    DoctorCheckResult,
    DoctorContext,
    DoctorProbe,
    DoctorStatus,
    build_context,
    run_preflight,
)

__all__ = [
    "DoctorCheckResult",
    "DoctorContext",
    "DoctorProbe",
    "DoctorStatus",
    "build_context",
    "doctor",
    "run_checks",
    "_check_python_dependencies",
    "run_preflight",
]

# Backwards-compatible test/extension hooks for the original CLI module surface.
_run = _preflight._run
_can_bind = _preflight._can_bind
_check_python = _preflight._check_python
_check_python_dependencies = _preflight._check_python_dependencies
_check_command = _preflight._check_command
_check_docker_daemon = _preflight._check_docker_daemon
_check_compose = _preflight._check_compose
_parse_db_url = _preflight._parse_db_url
_check_psql = _preflight._check_psql
_check_dir_writable = _preflight._check_dir_writable
_check_state_file = _preflight._check_state_file
_check_port = _preflight._check_port
_check_docker_conflicts = _preflight._check_docker_conflicts


def run_checks(
    db_url: str | None, state_file: Path, *, skip_db: bool = False
) -> list[DoctorCheckResult]:
    """Run host-readiness checks without requiring a loaded NetEngine spec."""
    # Keep monkeypatches of the legacy CLI module effective for callers/tests.
    _preflight._run = _run
    _preflight._can_bind = _can_bind
    return _preflight.run_checks(db_url, state_file, skip_db=skip_db)


def _print_report(results: Iterable[DoctorCheckResult]) -> None:
    _preflight._print_report(results)


@click.command("doctor")
@click.option(
    "--db-url",
    default=db_url_from_env,
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
    """Run local host preflight checks before booting; use diagnose for world health."""
    results = run_checks(db_url, state_file, skip_db=skip_db)
    if as_json:
        click.echo(json.dumps([asdict(r) for r in results], indent=2))
    else:
        click.echo("NetEngine doctor preflight report")
        _print_report(results)
    if any(r.status == DoctorStatus.FAIL and r.required for r in results):
        raise click.ClickException("required doctor checks failed")
