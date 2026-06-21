"""
Distributed tracing and context propagation support.

Implements:
- W3C Trace Context standard (traceparent, tracestate headers)
- Context local storage (contextvars)
- Automatic trace context injection into logs
- OpenTelemetry-compatible trace IDs
"""

import os
import uuid
import contextvars
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import re


# ============================================================================
# Constants
# ============================================================================

# W3C Trace Context format: version-trace_id-parent_id-trace_flags
# Example: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
TRACEPARENT_PATTERN = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)

# Context variables (thread-local with async support)
_trace_context: contextvars.ContextVar["TraceContext"] = contextvars.ContextVar(
    "trace_context",
    default=None,
)
_span_stack: contextvars.ContextVar[Optional[list[str]]] = contextvars.ContextVar(
    "span_stack",
    default=None,
)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class TraceContext:
    """
    W3C Trace Context representation.
    
    Attributes:
        trace_id: 32-character hex string (128-bit)
        parent_span_id: 16-character hex string (64-bit)
        span_id: 16-character hex string (64-bit) - generated for current operation
        trace_flags: 2-character hex string (sampled flag)
        is_sampled: Whether this trace is sampled
        tracestate: Vendor-specific trace state (optional)
    """
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    trace_flags: str = "01"  # Sampled=1
    tracestate: Optional[str] = None
    timestamp: float = None  # When context was created
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).timestamp()
    
    @property
    def is_sampled(self) -> bool:
        """Return whether this trace should be sampled."""
        return self.trace_flags[-1] == "1"
    
    @property
    def traceparent_header(self) -> str:
        """
        Return W3C traceparent header value.
        Format: 00-trace_id-span_id-trace_flags
        """
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"
    
    @classmethod
    def from_traceparent(cls, header: str, parent_span_id: Optional[str] = None) -> "TraceContext":
        """
        Parse W3C traceparent header.
        
        Args:
            header: traceparent header value
            parent_span_id: Override parent span ID
            
        Returns:
            TraceContext instance
            
        Raises:
            ValueError: If header format is invalid
        """
        match = TRACEPARENT_PATTERN.match(header)
        if not match:
            raise ValueError(f"Invalid traceparent format: {header}")
        
        trace_id, span_id, trace_flags = match.groups()
        
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id or span_id,
            trace_flags=trace_flags,
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for log injection."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "trace_flags": self.trace_flags,
            "is_sampled": self.is_sampled,
        }


# ============================================================================
# Trace Context Manager
# ============================================================================

class TraceContextManager:
    """
    Manages trace context across async and sync code.
    Handles context creation, propagation, and cleanup.
    """
    
    @staticmethod
    def create_trace_context(
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        trace_flags: str = "01",
    ) -> TraceContext:
        """
        Create a new trace context.
        
        Args:
            trace_id: Use existing trace ID, or generate new one
            parent_span_id: Parent operation's span ID
            trace_flags: W3C trace flags (sampled indicator)
            
        Returns:
            TraceContext instance
        """
        trace_id = trace_id or _generate_trace_id()
        span_id = _generate_span_id()
        
        context = TraceContext(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            trace_flags=trace_flags,
        )
        
        return context
    
    @staticmethod
    def set_context(context: TraceContext) -> None:
        """Set current trace context."""
        _trace_context.set(context)
    
    @staticmethod
    def get_context() -> Optional[TraceContext]:
        """Get current trace context."""
        return _trace_context.get()
    
    @staticmethod
    def clear_context() -> None:
        """Clear current trace context."""
        _trace_context.set(None)
    
    @staticmethod
    def from_headers(
        headers: Dict[str, str],
    ) -> Optional[TraceContext]:
        """
        Extract trace context from HTTP headers.
        
        Args:
            headers: HTTP headers dict
            
        Returns:
            TraceContext if traceparent header found, else None
        """
        traceparent = headers.get("traceparent") or headers.get("Traceparent")
        if not traceparent:
            return None
        
        try:
            return TraceContext.from_traceparent(traceparent)
        except ValueError:
            return None
    
    @staticmethod
    def push_span(span_id: Optional[str] = None) -> str:
        """
        Push a new span onto the stack.
        Creates a child span with current context as parent.
        
        Args:
            span_id: Use existing span ID, or generate new one
            
        Returns:
            New span ID
        """
        current_context = _trace_context.get()
        span_id = span_id or _generate_span_id()
        
        if current_context is None:
            # Create new trace context if needed
            current_context = TraceContextManager.create_trace_context()
            _trace_context.set(current_context)
        else:
            # Update parent span ID for nested context
            current_context.parent_span_id = current_context.span_id
            current_context.span_id = span_id
        
        stack = _span_stack.get() or []
        stack.append(span_id)
        _span_stack.set(stack)
        
        return span_id
    
    @staticmethod
    def pop_span() -> Optional[str]:
        """Pop the current span from the stack."""
        stack = _span_stack.get() or []
        if stack:
            return stack.pop()
        return None
    
    @staticmethod
    def get_span_depth() -> int:
        """Get current span stack depth."""
        return len(_span_stack.get() or [])


# ============================================================================
# Utility Functions
# ============================================================================

def _generate_trace_id() -> str:
    """Generate a W3C-compatible trace ID (128-bit, 32-char hex)."""
    return uuid.uuid4().hex


def _generate_span_id() -> str:
    """Generate a W3C-compatible span ID (64-bit, 16-char hex)."""
    return uuid.uuid4().hex[:16]


# ============================================================================
# Context Injection Utilities
# ============================================================================

class TraceInjector:
    """
    Injects trace context into various transport mechanisms.
    """
    
    @staticmethod
    def inject_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Inject trace context into HTTP headers for outbound requests.
        
        Args:
            headers: Existing headers dict, or None for new dict
            
        Returns:
            Headers dict with trace context
            
        Example:
            headers = inject_headers()
            response = requests.get("...", headers=headers)
        """
        if headers is None:
            headers = {}
        
        context = TraceContextManager.get_context()
        if context:
            headers["traceparent"] = context.traceparent_header
            if context.tracestate:
                headers["tracestate"] = context.tracestate
        
        return headers
    
    @staticmethod
    def get_log_context() -> dict:
        """
        Get trace context as dict suitable for log binding.
        
        Returns:
            Dict with trace fields, or empty dict if no context
        """
        context = TraceContextManager.get_context()
        if context:
            return context.to_dict()
        return {}


# ============================================================================
# Convenience Functions
# ============================================================================

def set_trace_context_from_headers(headers: Dict[str, str]) -> bool:
    """
    Extract and set trace context from HTTP headers.
    
    Args:
        headers: HTTP headers
        
    Returns:
        True if context was set, False if no traceparent found
    """
    context = TraceContextManager.from_headers(headers)
    if context:
        TraceContextManager.set_context(context)
        return True
    return False


def get_current_trace_id() -> Optional[str]:
    """Get current trace ID, or None."""
    context = TraceContextManager.get_context()
    return context.trace_id if context else None


def get_current_span_id() -> Optional[str]:
    """Get current span ID, or None."""
    context = TraceContextManager.get_context()
    return context.span_id if context else None


def inject_trace_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Inject current trace context into headers for outbound requests.
    
    Example:
        headers = inject_trace_headers()
        requests.get("...", headers=headers)
    """
    return TraceInjector.inject_headers(headers)


# ============================================================================
# Decorators
# ============================================================================

def with_trace_context(func):
    """
    Decorator that creates a new trace span for a function.
    
    Usage:
        @with_trace_context
        def process_request(request):
            ...
    """
    import functools
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        span_id = TraceContextManager.push_span()
        try:
            return func(*args, **kwargs)
        finally:
            TraceContextManager.pop_span()
    
    return wrapper
