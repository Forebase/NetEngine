"""OmegaConf structured configuration schemas."""

from dataclasses import dataclass, field
from typing import Any, Optional

from omegaconf import MISSING


@dataclass
class LoggingConfig:
    """Logging configuration schema."""

    environment: str = "development"
    debug: bool = False
    log_dir: str = "logs/"
    log_level: str = "DEBUG"
    retention_days: int = 30
    max_size_bytes: int = 10485760  # 10 MB
    sampling_enabled: bool = False
    sampling_rate: float = 0.1
    serialize_json: bool = False
    include_traceback: bool = True
    buffer_size: int = 1024


@dataclass
class SpecConfig:
    """Infrastructure specification configuration schema."""

    name: str = MISSING
    description: Optional[str] = None
    version: str = "1.0"
    organizations: list[dict[str, Any]] = field(default_factory=list)
    domains: list[dict[str, Any]] = field(default_factory=list)
    infrastructure: Optional[dict[str, Any]] = None
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    """Application configuration schema."""

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    spec: SpecConfig = field(default_factory=SpecConfig)
