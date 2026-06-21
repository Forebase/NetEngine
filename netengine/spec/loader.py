"""YAML spec loading and validation."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from netengine.spec.models import NetEngineSpec


class SpecLoadError(Exception):
    """Raised when spec loading or validation fails."""

    pass


def load_spec(yaml_path: str | Path) -> NetEngineSpec:
    """Load and validate a NetEngine YAML specification.

    Args:
        yaml_path: Path to YAML spec file

    Returns:
        Validated, immutable NetEngineSpec object

    Raises:
        SpecLoadError: If file not found or spec is invalid
    """
    yaml_path = Path(yaml_path)

    if not yaml_path.exists():
        raise SpecLoadError(f"Spec file not found: {yaml_path}")

    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise SpecLoadError(f"Failed to parse YAML: {e}")
    except IOError as e:
        raise SpecLoadError(f"Failed to read file: {e}")

    if not isinstance(data, dict):
        raise SpecLoadError("Spec must be a YAML object (dict)")

    try:
        spec = NetEngineSpec(**data)
    except ValidationError as e:
        raise SpecLoadError(f"Spec validation failed: {e}")

    return spec
