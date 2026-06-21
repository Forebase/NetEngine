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

from .core import (
    LoggerFactory,
    LogConfig,
    get_logger,
    log_context,
    timed_operation,
)

from .trace import (
    TraceContextManager,
    TraceContext,
    TraceInjector,
    set_trace_context_from_headers,
    get_current_trace_id,
    get_current_span_id,
    inject_trace_headers,
    with_trace_context,
)

from .middleware import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)

from .sinks import (
    CircuitBreaker,
    AsyncQueueSink,
    SupabaseSink,
    ErrorTrackingSink,
    PerformanceMetricsSink,
)

# Initialize logger on import
logger = LoggerFactory.get_logger(__name__)
