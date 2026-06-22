"""Regression tests for orchestrator spec normalization."""

from pathlib import Path

from netengine.config.loader import ConfigLoader
from netengine.core.orchestrator import Orchestrator
from netengine.spec.models import NetEngineSpec


def test_orchestrator_normalizes_example_yaml_dict_to_spec_model() -> None:
    """Raw example YAML dictionaries should become attribute-style spec models."""
    spec_path = Path(__file__).parent.parent / "examples" / "minimal.yaml"
    raw_spec = ConfigLoader.load_yaml(spec_path)

    orchestrator = Orchestrator(raw_spec)

    assert isinstance(orchestrator.spec, NetEngineSpec)
    assert isinstance(orchestrator.context.spec, NetEngineSpec)
    assert orchestrator.context.spec.dns.root.listen_ip == "10.0.0.2"
    assert orchestrator.context.spec.dns.platform_zone.name == "platform.internal"
    assert orchestrator.context.spec.metadata.name == "minimal-example"
    assert orchestrator.context.spec.substrate.gateway.core_ip == "10.0.0.1"
