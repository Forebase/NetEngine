"""Runtime application configuration helpers."""

import os
from pathlib import Path
from typing import Type

from netengine.config.loader import ConfigLoader
from netengine.config.schemas import AppConfig, LoggingConfig
from netengine.logs import LogConfig, LoggerFactory

NETENGINE_CONFIG_ENV = "NETENGINE_CONFIG"
_RUNTIME_DEFAULTS = {"spec": {"name": "runtime"}}


def _runtime_log_config(logging_config: LoggingConfig) -> Type[LogConfig]:
    """Convert AppConfig logging settings into a LoggerFactory-compatible config class."""

    class RuntimeLogConfig(LogConfig):
        ENV = logging_config.environment
        DEBUG = logging_config.debug
        LOG_DIR = Path(logging_config.log_dir)
        LOG_LEVEL = logging_config.log_level
        RETENTION_DAYS = logging_config.retention_days
        MAX_SIZE_BYTES = logging_config.max_size_bytes
        SAMPLING_ENABLED = logging_config.sampling_enabled
        SAMPLING_RATE = logging_config.sampling_rate
        SERIALIZE_JSON = logging_config.serialize_json
        INCLUDE_TRACEBACK = logging_config.include_traceback
        BUFFER_SIZE = logging_config.buffer_size

    RuntimeLogConfig.LOG_DIR.mkdir(parents=True, exist_ok=True)
    return RuntimeLogConfig


def load_app_config(config_file: str | Path | None = None) -> AppConfig:
    """Load AppConfig from an explicit path or NETENGINE_CONFIG."""
    selected_file = config_file if config_file is not None else os.environ.get(NETENGINE_CONFIG_ENV)
    return ConfigLoader.load_config(
        AppConfig,
        defaults=_RUNTIME_DEFAULTS,
        config_file=selected_file,
    )


def configure_logging(app_config: AppConfig, *, force: bool = True) -> Type[LogConfig]:
    """Apply AppConfig logging settings to the shared logger factory."""
    log_config = _runtime_log_config(app_config.logging)
    if force:
        LoggerFactory.shutdown()
    LoggerFactory.initialize(log_config)
    return log_config


def load_runtime_config(config_file: str | Path | None = None) -> AppConfig:
    """Load runtime config and apply runtime-only settings.

    AppConfig controls process/runtime concerns such as logging. World topology
    remains controlled by NetEngineSpec files passed to commands such as
    ``netengine up``.
    """
    app_config = load_app_config(config_file)
    configure_logging(app_config)
    return app_config
