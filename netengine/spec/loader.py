"""YAML spec loading and validation with OmegaConf composition support."""

import ipaddress
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

import netengine.logs as logs
from netengine.config.loader import ConfigLoader
from netengine.spec.models import SUPPORTED_SPEC_SCHEMA_VERSIONS, NetEngineSpec
from netengine.spec.types import GatewayRealInternetMode

logger = logs.getLogger(__name__)


class SpecLoadError(Exception):
    """Raised when spec loading or validation fails."""

    pass


def _resolve_feature_state_paths(spec: NetEngineSpec) -> Iterator[tuple[Any, str, Any, Any]]:
    """Yield feature-state entries with concrete paths, values, and defaults."""
    from collections.abc import Mapping

    from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined]

    from netengine.spec.feature_state import FEATURE_STATE_REGISTRY

    def _field_default(model: Any, field_name: str) -> Any:
        field = model.__class__.model_fields[field_name]
        if field.default is not PydanticUndefined:
            return field.default
        if field.default_factory is not None:
            return field.default_factory()
        return PydanticUndefined

    for entry in FEATURE_STATE_REGISTRY:
        parts = entry.path.split(".")
        nodes: list[tuple[str, Any, Any]] = [("", spec, PydanticUndefined)]
        for part in parts:
            next_nodes: list[tuple[str, Any, Any]] = []
            for prefix, node, default_node in nodes:
                if part == "*":
                    if isinstance(node, Mapping):
                        for key, value in node.items():
                            path = f"{prefix}.{key}" if prefix else str(key)
                            next_nodes.append((path, value, None))
                    continue

                if isinstance(node, Mapping):
                    if part not in node:
                        continue
                    value = node[part]
                    default_value = (
                        default_node.get(part) if isinstance(default_node, Mapping) else None
                    )
                else:
                    if not hasattr(node, part):
                        continue
                    value = getattr(node, part)
                    default_value = _field_default(node, part)

                path = f"{prefix}.{part}" if prefix else part
                next_nodes.append((path, value, default_value))
            nodes = next_nodes

        for concrete_path, value, default_value in nodes:
            yield entry, concrete_path, value, default_value


def _is_active_feature_value(value: Any, default_value: Any) -> bool:
    """Return True when a gated field is enabled or set to active non-default data."""
    if hasattr(value, "value"):
        comparable_value = value.value
    else:
        comparable_value = value

    if hasattr(default_value, "value"):
        comparable_default = default_value.value
    else:
        comparable_default = default_value

    if isinstance(comparable_value, bool):
        return comparable_value is True and comparable_value != comparable_default
    if comparable_value in (None, "", [], {}, (), set()):
        return False
    return bool(comparable_value != comparable_default)


def _validate_feature_states(spec: NetEngineSpec) -> None:
    """Validate unsupported spec fields and warn on experimental fields."""
    errors: list[str] = []

    for entry, path, value, default_value in _resolve_feature_state_paths(spec):
        if not _is_active_feature_value(value, default_value):
            continue
        message = f"{path} is {entry.state} in {entry.stage}: {entry.reason}"
        if entry.state == "unsupported":
            errors.append(message)
        elif entry.state == "experimental":
            logger.warning(message)

    if errors:
        raise SpecLoadError(
            "Unsupported spec features enabled:\n" + "\n".join(f"  - {e}" for e in errors)
        )


# Backwards-compatible name for callers/tests that imported the old helper.
def _warn_unsupported(spec: NetEngineSpec) -> None:
    """Validate feature-state metadata and warn for experimental fields."""
    _validate_feature_states(spec)


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

    # 5. Mirrored real-internet mode needs explicit IP allowlist targets.
    # The alpha gateway implementation renders nftables `ip daddr` rules from
    # service_mirrors, so hostnames would generate invalid nftables policy until
    # DNS aliasing/resolution is implemented.
    real_internet = spec.gateway_portal.real_internet
    if real_internet.mode == GatewayRealInternetMode.MIRRORED:
        if not real_internet.service_mirrors:
            errors.append(
                "gateway_portal.real_internet.service_mirrors: "
                "at least one service mirror is required when mode is 'mirrored'"
            )
    elif real_internet.service_mirrors:
        errors.append(
            "gateway_portal.real_internet.service_mirrors: "
            "service mirrors require real_internet.mode: 'mirrored'"
        )

    for idx, mirror in enumerate(real_internet.service_mirrors):
        try:
            ipaddress.IPv4Address(mirror.in_world_service)
        except ValueError:
            errors.append(
                f"gateway_portal.real_internet.service_mirrors[{idx}].in_world_service: "
                "must be an IPv4 address for alpha mirrored gateway nftables rules"
            )

    if errors:
        raise SpecLoadError(
            "Spec cross-validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def _validate_spec_data(
    data: dict[str, Any], *, validate_feature_states: bool
) -> NetEngineSpec:
    """Validate loaded spec data and return a NetEngineSpec model."""
    if not isinstance(data, dict):
        raise SpecLoadError("Spec must be a YAML object (dict)")

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise SpecLoadError("Spec metadata must be a YAML object (dict)")
    schema_version = metadata.get("schema_version")
    if schema_version is not None and schema_version not in SUPPORTED_SPEC_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SPEC_SCHEMA_VERSIONS))
        raise SpecLoadError(
            f"Unsupported spec metadata.schema_version {schema_version!r}; "
            f"supported versions: {supported}. Export with the older NetEngine version "
            "or migrate the spec before booting this release."
        )

    try:
        spec = NetEngineSpec(**data)
    except ValidationError as e:
        raise SpecLoadError(f"Spec validation failed: {e}")

    _cross_validate(spec)
    if validate_feature_states:
        _validate_feature_states(spec)
    return spec


def validate_spec_data(
    data: dict[str, Any], *, validate_feature_states: bool = True
) -> NetEngineSpec:
    """Validate composed raw spec data and return a ``NetEngineSpec``.

    Callers should compose data according to the project precedence contract
    before validation: structured model defaults < base spec <
    environment/spec file < explicit overrides/CLI ``--set``.
    """
    return _validate_spec_data(data, validate_feature_states=validate_feature_states)


def load_spec(yaml_path: str | Path, *, validate_feature_states: bool = True) -> NetEngineSpec:
    """Load and validate a single NetEngine YAML specification.

    A single file is applied over structured ``NetEngineSpec`` defaults during
    validation. Composition helpers use the broader precedence contract:
    structured defaults < base spec < environment/spec file < explicit
    overrides/CLI ``--set``.

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

    return _validate_spec_data(data, validate_feature_states=validate_feature_states)


def load_spec_with_composition(
    yaml_path: str | Path,
    base_path: Optional[str | Path] = None,
    overrides: Optional[dict[str, Any]] = None,
    validate_feature_states: bool = True,
) -> NetEngineSpec:
    """Load spec with optional base spec composition and overrides.

    Precedence is: structured ``NetEngineSpec`` defaults < ``base_path`` <
    ``yaml_path`` < ``overrides`` (including CLI ``--set`` values). Nested
    mappings are deep-merged, and later layers win for fields set in multiple
    places.

    Example:
        base spec: spec.base.yaml
        environment/spec override: spec.prod.yaml
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

    return _validate_spec_data(data, validate_feature_states=validate_feature_states)


def load_spec_with_environment(
    base_spec: str | Path,
    environment: str = "dev",
    overrides: Optional[dict[str, Any]] = None,
    validate_feature_states: bool = True,
) -> NetEngineSpec:
    """Load base spec and merge with environment-specific overrides.

    Precedence is: structured ``NetEngineSpec`` defaults < ``base_spec`` <
    ``spec.{environment}.yaml`` when present < ``overrides`` (including CLI
    ``--set`` values). Nested mappings are deep-merged, and later layers win
    for fields set in multiple places.

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

    return _validate_spec_data(data, validate_feature_states=validate_feature_states)
