"""Configuration loading and merging utilities."""

from pathlib import Path
from typing import Any, Optional, Type, TypeVar

import yaml
from omegaconf import OmegaConf

T = TypeVar("T")


class ConfigLoader:
    """Load and merge OmegaConf configurations."""

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

        OmegaConf.resolve(cfg)
        return OmegaConf.to_object(cfg)

    @staticmethod
    def merge_configs(*configs: dict[str, Any] | Any) -> dict[str, Any]:
        """Merge multiple configurations in order.

        Args:
            *configs: Configuration dictionaries to merge

        Returns:
            Merged configuration as dictionary
        """
        result = OmegaConf.create({})

        for cfg in configs:
            if isinstance(cfg, dict):
                cfg_obj = OmegaConf.create(cfg)
            else:
                cfg_obj = OmegaConf.create(OmegaConf.to_container(cfg))
            result = OmegaConf.merge(result, cfg_obj)

        return OmegaConf.to_container(result, resolve=True)  # type: ignore

    @staticmethod
    def resolve_env_vars(cfg: Any) -> Any:
        """Resolve environment variable interpolation in config.

        Args:
            cfg: Configuration object with potential ${env:VAR} interpolations

        Returns:
            Configuration with resolved environment variables
        """
        OmegaConf.resolve(cfg)
        return cfg
