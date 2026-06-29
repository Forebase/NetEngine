"""
Loguru Logging Framework

A comprehensive, production-grade logging implementation with:
- Structured logging with context binding
- W3C Trace Context for distributed tracing
- ASGI middleware integration
- Multiple sink implementations
- Circuit breaker pattern for failure handling
- Performance optimization (sampling, buffering, rotation)
"""

__version__ = "1.0.0"
__author__ = "Forebase Foundation"
__all__ = [
    # Core
    "LoggerFactory",
    "LogConfig",
    "get_logger",
    "getLogger",
    "Logger",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "log_context",
    "timed_operation",
    # Tracing
    "TraceContextManager",
    "TraceContext",
    "TraceInjector",
    "set_trace_context_from_headers",
    "get_current_trace_id",
    "get_current_span_id",
    "inject_trace_headers",
    "with_trace_context",
    # Middleware
    "LoggingMiddleware",
    "StructuredLoggingMiddleware",
    # Sinks
    "CircuitBreaker",
    "AsyncQueueSink",
    "SupabaseSink",
    "ErrorTrackingSink",
    "PerformanceMetricsSink",
]

import logging as _stdlib_logging
from loguru._logger import Logger

from .core import LogConfig, LoggerFactory, get_logger, log_context, timed_operation
from .middleware import LoggingMiddleware, StructuredLoggingMiddleware
from .sinks import (
    AsyncQueueSink,
    CircuitBreaker,
    ErrorTrackingSink,
    PerformanceMetricsSink,
    SupabaseSink,
)
from .trace import (
    TraceContext,
    TraceContextManager,
    TraceInjector,
    get_current_span_id,
    get_current_trace_id,
    inject_trace_headers,
    set_trace_context_from_headers,
    with_trace_context,
)

# Compatibility aliases for code/tests that previously used a logging-like module.
getLogger = get_logger
DEBUG = _stdlib_logging.DEBUG
INFO = _stdlib_logging.INFO
WARNING = _stdlib_logging.WARNING
ERROR = _stdlib_logging.ERROR
CRITICAL = _stdlib_logging.CRITICAL

# Initialize logger on import
logger = LoggerFactory.get_logger(__name__)
