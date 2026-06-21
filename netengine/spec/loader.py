"""YAML spec loading and validation with OmegaConf composition support."""

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from netengine.config.loader import ConfigLoader
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


def load_spec_with_composition(
    yaml_path: str | Path,
    base_path: Optional[str | Path] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> NetEngineSpec:
    """Load spec with optional base spec composition and overrides.

    Supports merging a base spec with environment-specific overrides.
    Example:
        base spec: spec.base.yaml
        environment override: spec.prod.yaml
        inline overrides: {"logging": {"level": "ERROR"}}

    Args:
        yaml_path: Path to main spec file
        base_path: Optional path to base spec to merge under
        overrides: Optional overrides dictionary

    Returns:
        Validated, immutable NetEngineSpec object

    Raises:
        SpecLoadError: If loading or validation fails
    """
    yaml_path = Path(yaml_path)

    if not yaml_path.exists():
        raise SpecLoadError(f"Spec file not found: {yaml_path}")

    try:
        data = ConfigLoader.load_yaml(yaml_path)

        if base_path:
            base_path_obj = Path(base_path)
            if not base_path_obj.exists():
                raise SpecLoadError(f"Base spec file not found: {base_path}")
            base_data = ConfigLoader.load_yaml(base_path)
            data = ConfigLoader.merge_configs(base_data, data)

        if overrides:
            data = ConfigLoader.merge_configs(data, overrides)

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


def load_spec_with_environment(
    base_spec: str | Path,
    environment: str = "dev",
    overrides: Optional[dict[str, Any]] = None,
) -> NetEngineSpec:
    """Load base spec and merge with environment-specific overrides.

    Automatically loads environment-specific spec file if it exists.
    Pattern: spec.base.yaml or spec.yaml + spec.{environment}.yaml

    Args:
        base_spec: Path to base spec file (e.g., spec.base.yaml or spec.yaml)
        environment: Environment name (dev, prod, staging, etc.)
        overrides: Optional additional overrides

    Returns:
        Validated, immutable NetEngineSpec object

    Raises:
        SpecLoadError: If loading or validation fails
    """
    base_path = Path(base_spec)
    base_dir = base_path.parent

    if not base_path.exists():
        raise SpecLoadError(f"Base spec file not found: {base_spec}")

    try:
        data = ConfigLoader.load_yaml(base_path)

        env_spec_name = f"spec.{environment}.yaml"
        env_spec_path = base_dir / env_spec_name

        if env_spec_path.exists():
            env_data = ConfigLoader.load_yaml(env_spec_path)
            data = ConfigLoader.merge_configs(data, env_data)

        if overrides:
            data = ConfigLoader.merge_configs(data, overrides)

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
