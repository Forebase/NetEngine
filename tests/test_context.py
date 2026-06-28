"""Tests for phase context defaults."""

from pathlib import Path

from netengine.core.state import RuntimeState
from netengine.handlers.context import PhaseContext
from netengine.spec.models import NetEngineSpec


def test_zone_dir_default_uses_cwd_at_construction_time(
    tmp_path: Path,
    monkeypatch,
    minimal_spec: NetEngineSpec,
    logger,
) -> None:
    """PhaseContext should derive zone_dir from cwd when it is constructed."""
    construction_cwd = tmp_path / "construction-cwd"
    construction_cwd.mkdir()

    monkeypatch.delenv("NETENGINE_ZONE_DIR", raising=False)
    monkeypatch.chdir(construction_cwd)

    context = PhaseContext(
        spec=minimal_spec,
        runtime_state=RuntimeState(),
        logger=logger,
    )

    assert context.zone_dir == str(construction_cwd / "data" / "coredns")
