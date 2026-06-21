"""
ASGI middleware for logging and trace context management.

Provides:
- Automatic trace context extraction from incoming requests
- Request/response logging
- Error handling and exception logging
- Performance measurement
- Structured context binding per-request
"""

from typing import Callable, Any, Optional
from datetime import datetime, timezone
import time
import json

from loguru import logger

from .core import get_logger
from .trace import TraceContextManager, TraceInjector, set_trace_context_from_headers


# ============================================================================
# Request/Response Logging Middleware
# ============================================================================

class LoggingMiddleware:
    """
    ASGI middleware for request/response logging and trace context management.
    
    Handles:
    - Extracting traceparent from incoming headers
    - Binding request context to logs
    - Logging request/response with timing
    - Error handling and exception logging
    
    Example (FastAPI):
        app.add_middleware(LoggingMiddleware)
    
    Example (Starlette):
        app.add_middleware(LoggingMiddleware)
    """
    
    def __init__(
        self,
        app,
        logger_instance=None,
        exclude_paths: Optional[list[str]] = None,
        include_body: bool = False,
        max_body_length: int = 1000,
    ):
        """
        Initialize logging middleware.
        
        Args:
            app: ASGI application
            logger_instance: Logger to use (default: get_logger)
            exclude_paths: Paths to exclude from logging (e.g., ["/health", "/metrics"])
            include_body: Whether to log request/response bodies
            max_body_length: Maximum body length to log (characters)
        """
        self.app = app
        self.logger = logger_instance or get_logger(__name__)
        self.exclude_paths = exclude_paths or ["/health", "/metrics", "/readiness"]
        self.include_body = include_body
        self.max_body_length = max_body_length
    
    async def __call__(self, scope, receive, send):
        """ASGI interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Skip excluded paths
        if self._should_exclude(scope["path"]):
            await self.app(scope, receive, send)
            return
        
        # Extract trace context from headers
        headers = dict(scope.get("headers", []))
        # Convert byte headers to strings
        headers = {k.decode() if isinstance(k, bytes) else k: 
                   v.decode() if isinstance(v, bytes) else v
                   for k, v in headers.items()}
        
        trace_context_set = set_trace_context_from_headers(headers)
        if not trace_context_set:
            # Create new trace context for this request
            TraceContextManager.set_context(
                TraceContextManager.create_trace_context()
            )
        
        # Bind request context
        request_context = self._extract_request_context(scope, headers)
        request_logger = self.logger.bind(**request_context)
        
        # Log incoming request
        request_logger.info(
            "HTTP request received",
            extra={
                "event_type": "http.request",
                "http_method": scope["method"],
                "http_path": scope["path"],
                "http_query": scope.get("query_string", b"").decode(),
                "http_scheme": scope.get("scheme", "http"),
            }
        )
        
        start_time = time.time()
        status_code = 500
        response_headers = None
        
        async def send_wrapper(message):
            """Intercept response to capture status and headers."""
            nonlocal status_code, response_headers
            
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = message.get("headers", [])
            
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
        
        except Exception as e:
            # Log exception
            duration = time.time() - start_time
            request_logger.error(
                f"HTTP request failed: {type(e).__name__}",
                extra={
                    "event_type": "http.error",
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "duration_ms": duration * 1000,
                    "status_code": 500,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }
            )
            raise
        
        else:
            # Log response
            duration = time.time() - start_time
            log_level = self._get_log_level(status_code)
            
            request_logger.log(
                log_level,
                "HTTP request completed",
                extra={
                    "event_type": "http.response",
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "http_status": status_code,
                    "duration_ms": duration * 1000,
                }
            )
        
        finally:
            # Clean up trace context
            TraceContextManager.clear_context()
    
    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded from logging."""
        return any(path.startswith(exclude) for exclude in self.exclude_paths)
    
    def _extract_request_context(self, scope: dict, headers: dict) -> dict:
        """Extract structured context from request."""
        context = {
            "http_method": scope["method"],
            "http_path": scope["path"],
            "http_scheme": scope.get("scheme", "http"),
            "client_host": scope.get("client", ["unknown", 0])[0],
            "client_port": scope.get("client", ["unknown", 0])[1],
        }
        
        # Add trace context
        trace_context = TraceContextManager.get_context()
        if trace_context:
            context.update(trace_context.to_dict())
        
        # Add user agent if present
        user_agent = headers.get("user-agent") or headers.get("User-Agent")
        if user_agent:
            context["user_agent"] = user_agent
        
        # Add request ID if present
        request_id = (headers.get("x-request-id") or 
                      headers.get("X-Request-ID") or
                      headers.get("X-Correlation-ID"))
        if request_id:
            context["request_id"] = request_id
        
        return context
    
    @staticmethod
    def _get_log_level(status_code: int) -> str:
        """Determine log level based on HTTP status code."""
        if status_code < 400:
            return "INFO"
        elif status_code < 500:
            return "WARNING"
        else:
            return "ERROR"


# ============================================================================
# Structured Logging Middleware
# ============================================================================

class StructuredLoggingMiddleware:
    """
    Enhanced logging middleware with structured logging patterns.
    
    Extends LoggingMiddleware with:
    - Request/response body logging (with truncation)
    - Custom field extraction
    - Request correlation IDs
    - Performance thresholds for slow requests
    """
    
    def __init__(
        self,
        app,
        logger_instance=None,
        exclude_paths: Optional[list[str]] = None,
        slow_request_threshold_ms: float = 1000.0,
        extract_user_id: Optional[Callable] = None,
        extract_custom_fields: Optional[Callable] = None,
    ):
        """
        Initialize structured logging middleware.
        
        Args:
            app: ASGI application
            logger_instance: Logger to use
            exclude_paths: Paths to exclude from logging
            slow_request_threshold_ms: Threshold for "slow request" warning
            extract_user_id: Callable to extract user ID from scope
            extract_custom_fields: Callable to extract custom fields from scope
        """
        self.app = app
        self.logger = logger_instance or get_logger(__name__)
        self.exclude_paths = exclude_paths or ["/health", "/metrics"]
        self.slow_request_threshold_ms = slow_request_threshold_ms
        self.extract_user_id = extract_user_id
        self.extract_custom_fields = extract_custom_fields
    
    async def __call__(self, scope, receive, send):
        """ASGI interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        if any(scope["path"].startswith(p) for p in self.exclude_paths):
            await self.app(scope, receive, send)
            return
        
        # Set up trace context
        headers = dict(scope.get("headers", []))
        headers = {k.decode() if isinstance(k, bytes) else k:
                   v.decode() if isinstance(v, bytes) else v
                   for k, v in headers.items()}
        
        set_trace_context_from_headers(headers)
        
        # Build context
        context = {
            "method": scope["method"],
            "path": scope["path"],
            "query": scope.get("query_string", b"").decode(),
            "scheme": scope.get("scheme", "http"),
            "client": scope.get("client", ("unknown", 0))[0],
        }
        
        # Extract user ID if provided
        if self.extract_user_id:
            try:
                user_id = self.extract_user_id(scope)
                if user_id:
                    context["user_id"] = user_id
            except Exception as e:
                self.logger.warning(f"Failed to extract user ID: {e}")
        
        # Extract custom fields if provided
        if self.extract_custom_fields:
            try:
                custom_fields = self.extract_custom_fields(scope)
                context.update(custom_fields)
            except Exception as e:
                self.logger.warning(f"Failed to extract custom fields: {e}")
        
        # Add trace context
        trace_context = TraceContextManager.get_context()
        if trace_context:
            context.update(trace_context.to_dict())
        
        request_logger = self.logger.bind(**context)
        
        # Log request
        request_logger.info(
            f"{scope['method']} {scope['path']}",
            extra={"event": "http.request.start"}
        )
        
        start_time = time.time()
        status_code = 500
        
        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            request_logger.error(
                f"Request failed: {type(e).__name__}",
                extra={
                    "event": "http.error",
                    "status": 500,
                    "duration_ms": duration_ms,
                }
            )
            raise
        else:
            duration_ms = (time.time() - start_time) * 1000
            
            # Check for slow request
            if duration_ms > self.slow_request_threshold_ms:
                request_logger.warning(
                    f"Slow request: {scope['method']} {scope['path']}",
                    extra={
                        "event": "http.slow_request",
                        "duration_ms": duration_ms,
                        "threshold_ms": self.slow_request_threshold_ms,
                        "status": status_code,
                    }
                )
            else:
                request_logger.info(
                    f"{scope['method']} {scope['path']} {status_code}",
                    extra={
                        "event": "http.response.complete",
                        "status": status_code,
                        "duration_ms": duration_ms,
                    }
                )
        
        finally:
            TraceContextManager.clear_context()
