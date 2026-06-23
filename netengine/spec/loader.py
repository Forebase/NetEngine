"""YAML spec loading and validation with OmegaConf composition support."""

import ipaddress
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from netengine.config.loader import ConfigLoader
from netengine.spec.models import NetEngineSpec


class SpecLoadError(Exception):
    """Raised when spec loading or validation fails."""

    pass


def _cross_validate(spec: NetEngineSpec) -> None:
    """Cross-field validation not expressible in Pydantic field validators.

    Raises SpecLoadError listing all violations found (not just the first).
    """
    errors: list[str] = []

    # 1. AND instance names must be unique
    and_names = [i.name for i in spec.ands.instances]
    seen: set[str] = set()
    for name in and_names:
        if name in seen:
            errors.append(f"Duplicate AND instance name: '{name}'")
        seen.add(name)

    # 2. AND instance org references must match declared organizations
    org_names = {o.name for o in spec.world_registry.organizations}
    for inst in spec.ands.instances:
        if org_names and inst.org not in org_names:
            errors.append(f"AND instance '{inst.name}' references unknown org '{inst.org}'")

    # 3. AND instance profile references must exist in profiles dict
    for inst in spec.ands.instances:
        if spec.ands.profiles and inst.profile not in spec.ands.profiles:
            errors.append(f"AND instance '{inst.name}' references unknown profile '{inst.profile}'")

    # 4. Substrate network CIDRs must be valid and non-overlapping
    parsed_nets: list[tuple[str, ipaddress.IPv4Network]] = []
    for net_name, net_cfg in spec.substrate.networks.items():
        try:
            net = ipaddress.IPv4Network(net_cfg.subnet, strict=False)
            parsed_nets.append((net_name, net))
        except ValueError:
            errors.append(f"substrate.networks.{net_name}: invalid CIDR '{net_cfg.subnet}'")

    for i, (name_a, net_a) in enumerate(parsed_nets):
        for name_b, net_b in parsed_nets[i + 1 :]:
            if net_a.overlaps(net_b):
                errors.append(f"subnet overlap: {name_a} ({net_a}) overlaps {name_b} ({net_b})")

    if errors:
        raise SpecLoadError(
            "Spec cross-validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )


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

    _cross_validate(spec)
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

    _cross_validate(spec)
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

    _cross_validate(spec)
    return spec
