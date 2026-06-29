"""Configuration loading and merging utilities."""

from collections.abc import Iterable
import os
from pathlib import Path
from typing import Any, Optional, Type, TypeVar, cast

import yaml
from omegaconf import OmegaConf


def _get_required_env_var(name: str) -> str:
    """Return an environment variable or raise for missing values."""
    try:
        return os.environ[name]
    except KeyError as exc:
        raise KeyError(f"Environment variable '{name}' not found") from exc


def register_env_resolver() -> None:
    """Register NetEngine's custom ``${env:VAR}`` OmegaConf resolver."""
    if not OmegaConf.has_resolver("env"):
        OmegaConf.register_new_resolver("env", _get_required_env_var)


register_env_resolver()


class ConfigOverrideError(ValueError):
    """Raised when a dotted configuration override cannot be parsed."""


def parse_dotted_overrides(values: Iterable[str]) -> dict[str, Any]:
    """Convert dotted ``key=value`` overrides into a nested dictionary.

    Values are parsed with ``yaml.safe_load`` so CLI strings such as
    ``true``, ``42``, or ``[a, b]`` become YAML-native scalars/lists.

    Args:
        values: Override strings in dotted ``key=value`` form.

    Returns:
        Nested dictionary suitable for merging into a configuration.

    Raises:
        ConfigOverrideError: If an override is malformed or attempts to nest
            beneath a scalar value already set by another override.
    """
    overrides: dict[str, Any] = {}

    for item in values:
        key, separator, raw_value = item.partition("=")
        if not separator:
            raise ConfigOverrideError("must be in key=value form")

        parts = key.split(".")
        if any(part == "" for part in parts):
            raise ConfigOverrideError("keys must be non-empty dotted paths")

        value = yaml.safe_load(raw_value)
        cursor = overrides
        for part in parts[:-1]:
            existing = cursor.get(part)
            if existing is None:
                nested: dict[str, Any] = {}
                cursor[part] = nested
                cursor = nested
            elif isinstance(existing, dict):
                cursor = existing
            else:
                raise ConfigOverrideError(f"cannot set nested key under non-object path '{part}'")
        cursor[parts[-1]] = value

    return overrides


T = TypeVar("T")


class ConfigLoader:
    """Load and merge OmegaConf configurations.

    Precedence is intentionally last-writer-wins for all helpers in this
    module. The contract is: structured schema defaults < caller-provided
    defaults < config/spec file data < explicit overrides such as CLI
    ``--set`` values. Nested mappings are deep-merged at each layer, so a
    higher-precedence layer can replace one nested field without discarding
    sibling fields from lower-precedence layers.
    """

    @staticmethod
    def load_yaml(path: Path | str) -> dict[str, Any]:
        """Load YAML file as dictionary.

        Args:
            path: Path to YAML file

        Returns:
            Parsed YAML content as dictionary
        """
        path = Path(path)
        with open(path) as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def load_config(
        config_schema: Type[T],
        defaults: Optional[dict[str, Any]] = None,
        config_file: Optional[Path | str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> T:
        """Load configuration with optional file and overrides.

        Merge precedence is: structured schema defaults < ``defaults`` <
        ``config_file`` < ``overrides``. Later layers win for the same field,
        while nested dictionaries are deep-merged. Environment variables may be
        interpolated with OmegaConf's built-in ``${oc.env:VAR}`` syntax or
        NetEngine's equivalent ``${env:VAR}`` alias. For example, a YAML file
        containing ``metadata: {owner: "${env:NETENGINE_OWNER}"}`` resolves
        ``owner`` from the ``NETENGINE_OWNER`` environment variable. Missing
        variables raise an interpolation error.

        Args:
            config_schema: OmegaConf dataclass schema
            defaults: Default configuration dictionary
            config_file: Optional config file to load
            overrides: Optional overrides dictionary

        Returns:
            Merged configuration object
        """
        cfg = OmegaConf.structured(config_schema)

        if defaults:
            defaults_cfg = OmegaConf.create(defaults)
            cfg = OmegaConf.merge(cfg, defaults_cfg)

        if config_file:
            config_dict = ConfigLoader.load_yaml(config_file)
            file_cfg = OmegaConf.create(config_dict)
            cfg = OmegaConf.merge(cfg, file_cfg)

        if overrides:
            overrides_cfg = OmegaConf.create(overrides)
            cfg = OmegaConf.merge(cfg, overrides_cfg)

        register_env_resolver()
        OmegaConf.resolve(cfg)
        return cast(T, OmegaConf.to_object(cfg))

    @staticmethod
    def merge_configs(*configs: dict[str, Any] | Any) -> dict[str, Any]:
        """Merge multiple configurations in order.

        Later positional arguments have higher precedence than earlier ones.
        Nested dictionaries are deep-merged, allowing higher-precedence layers
        to override individual nested fields while preserving siblings.

        Args:
            *configs: Configuration dictionaries to merge

        Returns:
            Merged configuration as dictionary
        """
        result: Any = OmegaConf.create({})

        for cfg in configs:
            if isinstance(cfg, dict):
                cfg_obj: Any = OmegaConf.create(cfg)
            else:
                cfg_obj = OmegaConf.create(OmegaConf.to_container(cfg))
            result = OmegaConf.merge(result, cfg_obj)

        return cast(dict[str, Any], OmegaConf.to_container(result, resolve=True))

    @staticmethod
    def resolve_env_vars(cfg: Any) -> Any:
        """Resolve environment variable interpolation in config.

        Supports both OmegaConf's built-in ``${oc.env:VAR}`` syntax and
        NetEngine's custom ``${env:VAR}`` alias. For example,
        ``OmegaConf.create({"token": "${env:NETENGINE_TOKEN}"})`` resolves
        ``token`` from ``NETENGINE_TOKEN``. Missing variables raise an
        interpolation error.

        Args:
            cfg: Configuration object with potential environment interpolations

        Returns:
            Configuration with resolved environment variables
        """
        register_env_resolver()
        OmegaConf.resolve(cfg)
        return cfg
