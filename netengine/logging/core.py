"""
Core Loguru configuration and initialization.

Provides:
- Flexible logger setup with environment-aware configuration
- Rotation and retention policies
- Multiple sink management
- Custom formatting and filtering
- Performance-conscious initialization
"""

import json
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Generator, Optional

from loguru import logger as _logger

if TYPE_CHECKING:
    from loguru import Logger, Record


# ============================================================================
# Configuration Classes
# ============================================================================


class LogConfig:
    """Centralized logging configuration."""

    # Environment detection
    ENV = os.getenv("ENVIRONMENT", "development")
    DEBUG = ENV == "development" or os.getenv("DEBUG", "").lower() == "true"

    # Paths
    LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Log levels
    LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG" if DEBUG else "INFO")

    # Retention
    RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))
    MAX_SIZE_BYTES = int(os.getenv("LOG_MAX_SIZE", str(10 * 1024 * 1024)))  # 10 MB

    # Sampling
    SAMPLING_ENABLED = not DEBUG
    SAMPLING_RATE = float(os.getenv("LOG_SAMPLING_RATE", "0.1"))  # 10% when enabled

    # Performance
    SERIALIZE_JSON = os.getenv("LOG_SERIALIZE", "true").lower() == "true"
    INCLUDE_TRACEBACK = DEBUG
    BUFFER_SIZE = int(os.getenv("LOG_BUFFER_SIZE", "512"))  # bytes


# ============================================================================
# Formatting Functions
# ============================================================================


def format_record_dev(record: "Record") -> str:
    """
    Developer-friendly format with colors and context.
    Used in development environments.
    """
    timestamp = record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    level = record["level"].name
    name = record["name"]

    # Add context information if present
    context = record.get("extra", {})
    context_str = ""
    if context:
        context_items = [f"{k}={v}" for k, v in context.items()]
        context_str = " [" + " ".join(context_items) + "]"

    message = record["message"]

    return (
        f"<level>{timestamp} {level:8}</level> | "
        f"<cyan>{name}</cyan> | "
        f"{message}{context_str}\n"
    )


def format_record_json(record: "Record") -> str:
    """
    JSON format for structured logging.
    Used in production; machine-parseable.
    """
    log_entry = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "process": {
            "id": record["process"].id,
            "name": record["process"].name,
        },
        "thread": {
            "id": record["thread"].id,
            "name": record["thread"].name,
        },
    }

    # Include exception info if present
    if record["exception"] and record["exception"].type is not None:
        log_entry["exception"] = {
            "type": record["exception"].type.__name__,
            "value": str(record["exception"].value),
            "traceback": record["message"],
        }

    # Include context/extra fields
    if record.get("extra"):
        log_entry["context"] = record["extra"]

    # Include trace context if present (for distributed tracing)
    if "trace_id" in record["extra"]:
        log_entry["trace"] = {
            "trace_id": record["extra"].get("trace_id"),
            "span_id": record["extra"].get("span_id"),
            "parent_span_id": record["extra"].get("parent_span_id"),
        }

    return json.dumps(log_entry) + "\n"


# ============================================================================
# Filters
# ============================================================================


class SamplingFilter:
    """
    Probabilistic sampling filter.
    Reduces log volume in high-throughput scenarios by sampling.
    Always passes ERROR and CRITICAL.
    """

    def __init__(self, rate: float = 0.1):
        """
        Args:
            rate: Sampling rate (0.0 - 1.0). 0.1 = 10% of logs.
        """
        if not 0.0 <= rate <= 1.0:
            raise ValueError("Sampling rate must be between 0.0 and 1.0")
        self.rate = rate
        self._lock = threading.Lock()
        self._sample_count = 0
        self._total_count = 0

    def __call__(self, record: "Record") -> bool:
        # Always log ERROR and CRITICAL
        if record["level"].no >= 40:  # WARNING=30, ERROR=40, CRITICAL=50
            return True

        # Sample other levels
        import random

        with self._lock:
            self._total_count += 1
            if random.random() < self.rate:
                self._sample_count += 1
                return True

        return False

    def stats(self) -> dict[str, Any]:
        """Return sampling statistics."""
        with self._lock:
            rate = self._sample_count / self._total_count * 100 if self._total_count > 0 else 0
            return {
                "sampled": self._sample_count,
                "total": self._total_count,
                "actual_rate": rate,
            }


class ContextFilter:
    """
    Ensures context-relevant fields are always present.
    Prevents AttributeError when accessing record["extra"] fields.
    """

    def __call__(self, record: "Record") -> bool:
        # Ensure extra dict exists
        if "extra" not in record:
            record["extra"] = {}
        return True


class NoiseFilter:
    """
    Suppress overly chatty loggers (e.g., urllib3, asyncio).
    Configurable per-logger basis.
    """

    def __init__(self, noisy_loggers: Optional[list[str]] = None):
        self.noisy_loggers = noisy_loggers or [
            "urllib3",
            "asyncio",
            "aiohttp",
            "websockets",
            "boto3",
        ]

    def __call__(self, record: "Record") -> bool:
        logger_name = record["name"] or ""
        return not any(logger_name.startswith(noisy) for noisy in self.noisy_loggers)


# ============================================================================
# Logger Initialization
# ============================================================================


class LoggerFactory:
    """
    Centralized logger setup and management.
    Handles configuration, sink registration, and lifecycle.
    """

    _initialized = False
    _sinks: list[str] = []
    _lock = threading.Lock()

    @classmethod
    def initialize(
        cls,
        config: type[LogConfig] = LogConfig,
        extra: Optional[dict[str, Any]] = None,
        add_default_sinks: bool = True,
    ) -> None:
        """
        Initialize the logger with configuration and sinks.

        Args:
            config: LogConfig instance or class
            extra: Additional context to bind to all logs
            add_default_sinks: Whether to add stdout and file sinks
        """
        if cls._initialized:
            return

        with cls._lock:
            # Remove default handler
            _logger.remove()

            # Bind default context
            default_context = {
                "env": config.ENV,
                "version": os.getenv("APP_VERSION", "unknown"),
            }
            if extra:
                default_context.update(extra)

            _logger.bind(**default_context)

            # Add default sinks
            if add_default_sinks:
                cls.add_stdout_sink(config)
                cls.add_file_sink(config)

            cls._initialized = True

    @classmethod
    def add_stdout_sink(cls, config: type[LogConfig] = LogConfig) -> None:
        """Add colored stdout sink (development-friendly)."""
        formatter = format_record_dev if not config.SERIALIZE_JSON else format_record_json
        noise_filter = NoiseFilter()

        _logger.add(
            sys.stdout,
            format=formatter,
            level=config.LOG_LEVEL,
            colorize=True,
            backtrace=config.INCLUDE_TRACEBACK,
            diagnose=config.DEBUG,
            filter=noise_filter,
        )
        cls._sinks.append("stdout")

    @classmethod
    def add_file_sink(
        cls,
        config: type[LogConfig] = LogConfig,
        name: str = "app",
    ) -> None:
        """
        Add rotating file sink.

        Args:
            config: LogConfig instance
            name: Log file base name
        """
        log_file = config.LOG_DIR / f"{name}.log"

        formatter = format_record_json if config.SERIALIZE_JSON else format_record_dev

        max_size = config.MAX_SIZE_BYTES
        _logger.add(
            str(log_file),
            format=formatter,
            level=config.LOG_LEVEL,
            rotation=max_size,
            retention=f"{config.RETENTION_DAYS} days",
            compression="gz",
            backtrace=config.INCLUDE_TRACEBACK,
            diagnose=config.DEBUG,
        )
        cls._sinks.append(f"file:{name}")

    @classmethod
    def add_custom_sink(
        cls,
        sink: Callable[..., Any],
        level: str = "INFO",
        format: Optional[Callable[..., str]] = None,
    ) -> None:
        """
        Add custom sink (e.g., external service, database).

        Args:
            sink: Callable sink function
            level: Log level for this sink
            format: Custom formatter
        """
        _logger.add(
            sink,
            format=format
            or (format_record_json if LogConfig.SERIALIZE_JSON else format_record_dev),
            level=level,
        )
        cls._sinks.append("custom")

    @classmethod
    def get_logger(cls, name: str = __name__) -> "Logger":
        """Get named logger instance."""
        if not cls._initialized:
            cls.initialize()
        return _logger.bind(logger_name=name)

    @classmethod
    def shutdown(cls) -> None:
        """Gracefully shutdown logger and close all sinks."""
        _logger.remove()
        cls._sinks.clear()
        cls._initialized = False


# ============================================================================
# Convenience Functions
# ============================================================================


def get_logger(name: str) -> "Logger":
    """Get a logger instance bound to a module name."""
    return LoggerFactory.get_logger(name)


@contextmanager
def log_context(**kwargs: Any) -> Generator["Logger", None, None]:
    """
    Context manager for temporary context binding.

    Usage:
        with log_context(user_id=123, request_id="abc"):
            logger.info("Processing user")
    """
    bound = get_logger(__name__).bind(**kwargs)
    yield bound


@contextmanager
def timed_operation(
    operation_name: str, logger_instance: Optional["Logger"] = None
) -> Generator[None, None, None]:
    """
    Context manager for timing operations and logging duration.

    Usage:
        with timed_operation("database_query"):
            db.execute(...)
    """
    if logger_instance is None:
        logger_instance = get_logger(__name__)

    start = datetime.now(timezone.utc)
    logger_instance.info(f"Starting: {operation_name}")

    try:
        yield
    except Exception:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        logger_instance.error(
            f"Failed: {operation_name}",
            extra={
                "operation": operation_name,
                "duration_seconds": duration,
                "status": "error",
            },
        )
        raise
    else:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        logger_instance.info(
            f"Completed: {operation_name}",
            extra={
                "operation": operation_name,
                "duration_seconds": duration,
                "status": "success",
            },
        )


# ============================================================================
# Module Initialization
# ============================================================================

# Initialize logger on import (can be customized later)
logger = _logger
