"""Infrastructure specification configuration and loading."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from omegaconf import OmegaConf

from netengine.config.loader import ConfigLoader

if TYPE_CHECKING:
    from netengine.spec.models import NetEngineSpec


class SpecConfig:
    """Load and manage infrastructure specifications with composition support."""

    @staticmethod
    def load_raw(
        spec_path: Path | str,
        base_path: Optional[Path | str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Load spec with optional base spec composition and overrides.

        Args:
            spec_path: Path to main spec file
            base_path: Optional path to base spec to merge under
            overrides: Optional overrides dictionary

        Returns:
            Loaded and merged raw specification as dictionary
        """
        spec_path = Path(spec_path)

        spec_dict = ConfigLoader.load_yaml(spec_path)

        if base_path:
            base_path = Path(base_path)
            base_dict = ConfigLoader.load_yaml(base_path)
            spec_dict = ConfigLoader.merge_configs(base_dict, spec_dict)

        if overrides:
            spec_dict = ConfigLoader.merge_configs(spec_dict, overrides)

        return spec_dict

    @staticmethod
    def load(
        spec_path: Path | str,
        base_path: Optional[Path | str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Load spec as a raw dictionary.

        This backwards-compatible alias preserves the historical ``SpecConfig.load``
        behavior. Prefer ``load_raw`` for new raw merge callers or
        ``load_validated`` when a validated ``NetEngineSpec`` is required.
        """
        return SpecConfig.load_raw(spec_path, base_path=base_path, overrides=overrides)

    @staticmethod
    def load_validated(
        spec_path: Path | str,
        base_path: Optional[Path | str] = None,
        overrides: Optional[dict[str, Any]] = None,
        validate_feature_states: bool = True,
    ) -> "NetEngineSpec":
        """Load, compose, and validate a spec into a ``NetEngineSpec``."""
        from netengine.spec.loader import validate_spec_data

        spec_dict = SpecConfig.load_raw(spec_path, base_path=base_path, overrides=overrides)
        return validate_spec_data(spec_dict, validate_feature_states=validate_feature_states)

    @staticmethod
    def load_environment_variants_raw(
        base_spec: Path | str,
        environment: str = "dev",
        overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Load base spec and merge with environment-specific overrides.

        Pattern:
            spec.base.yaml or spec.yaml (base)
            spec.{environment}.yaml (environment overrides)

        Args:
            base_spec: Path to base spec file (e.g., spec.base.yaml or spec.yaml)
            environment: Environment name (dev, prod, staging, etc.)
            overrides: Optional additional overrides

        Returns:
            Merged raw specification dictionary
        """
        base_path = Path(base_spec)
        base_dir = base_path.parent

        spec_dict = ConfigLoader.load_yaml(base_path)

        env_spec_name = f"spec.{environment}.yaml"
        env_spec_path = base_dir / env_spec_name

        if env_spec_path.exists():
            env_dict = ConfigLoader.load_yaml(env_spec_path)
            spec_dict = ConfigLoader.merge_configs(spec_dict, env_dict)

        if overrides:
            spec_dict = ConfigLoader.merge_configs(spec_dict, overrides)

        return spec_dict

    @staticmethod
    def load_environment_variants(
        base_spec: Path | str,
        environment: str = "dev",
        overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Load environment variants as a raw dictionary.

        This backwards-compatible alias preserves the historical
        ``SpecConfig.load_environment_variants`` behavior. Prefer
        ``load_environment_variants_raw`` for new raw merge callers or
        ``load_environment_variant_validated`` when a validated ``NetEngineSpec``
        is required.
        """
        return SpecConfig.load_environment_variants_raw(
            base_spec, environment=environment, overrides=overrides
        )

    @staticmethod
    def load_environment_variant_validated(
        base_spec: Path | str,
        environment: str = "dev",
        overrides: Optional[dict[str, Any]] = None,
        validate_feature_states: bool = True,
    ) -> "NetEngineSpec":
        """Load an environment variant and validate it into a ``NetEngineSpec``."""
        from netengine.spec.loader import validate_spec_data

        spec_dict = SpecConfig.load_environment_variants_raw(
            base_spec, environment=environment, overrides=overrides
        )
        return validate_spec_data(spec_dict, validate_feature_states=validate_feature_states)

    @staticmethod
    def to_dict(spec_obj: Any) -> dict[str, Any]:
        """Convert OmegaConf spec object to dictionary.

        Args:
            spec_obj: OmegaConf configuration object

        Returns:
            Dictionary representation
        """
        return OmegaConf.to_container(spec_obj, resolve=True)  # type: ignore
