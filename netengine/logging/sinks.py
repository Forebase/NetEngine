"""
Advanced sink implementations for external log aggregation.

Provides:
- Supabase/PostgreSQL sink for audit logs and structured logging
- Error tracking sink (Sentry-style)
- Performance metrics sink
- Circuit breaker pattern for sink failures
- Async sink wrappers
"""

import json
import queue
import threading
from typing import Optional, Callable, Any
from datetime import datetime, timezone
from dataclasses import dataclass
from abc import ABC, abstractmethod
import traceback

from loguru import logger


# ============================================================================
# Circuit Breaker Pattern
# ============================================================================

class CircuitBreaker:
    """
    Prevents cascading failures when external services are down.
    Implements half-open state for recovery detection.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "CircuitBreaker",
    ):
        """
        Args:
            failure_threshold: Failures before opening circuit
            recovery_timeout: Seconds before attempting recovery
            name: Circuit breaker name (for logging)
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open
        self._lock = threading.Lock()
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Callable to execute
            *args, **kwargs: Arguments to pass to func
            
        Returns:
            Result of func() if circuit is closed
            
        Raises:
            CircuitBreakerOpen: If circuit is open
        """
        with self._lock:
            if self.state == "open":
                if self._should_attempt_recovery():
                    self.state = "half-open"
                else:
                    raise CircuitBreakerOpen(f"{self.name} is open")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed for recovery attempt."""
        if not self.last_failure_time:
            return True
        elapsed = datetime.now(timezone.utc).timestamp() - self.last_failure_time
        return elapsed >= self.recovery_timeout
    
    def _on_success(self) -> None:
        """Handle successful call."""
        with self._lock:
            self.failure_count = 0
            self.state = "closed"
    
    def _on_failure(self) -> None:
        """Handle failed call."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now(timezone.utc).timestamp()
            if self.failure_count >= self.failure_threshold:
                self.state = "open"
    
    def get_status(self) -> dict:
        """Get circuit breaker status."""
        with self._lock:
            return {
                "state": self.state,
                "failure_count": self.failure_count,
                "last_failure": self.last_failure_time,
            }


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open."""
    pass


# ============================================================================
# Async Queue Sink
# ============================================================================

class AsyncQueueSink:
    """
    Async-friendly sink that queues logs and processes them in background.
    Prevents blocking when external services are slow.
    """
    
    def __init__(
        self,
        process_func: Callable,
        queue_size: int = 1000,
        batch_size: int = 10,
        batch_timeout: float = 5.0,
    ):
        """
        Args:
            process_func: Async function to process log records
            queue_size: Maximum queue size
            batch_size: Logs to batch before processing
            batch_timeout: Seconds before processing partial batch
        """
        self.process_func = process_func
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        
        self.queue = queue.Queue(maxsize=queue_size)
        self.batch = []
        self.last_flush = datetime.now(timezone.utc).timestamp()
        
        # Start background worker
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"AsyncQueueSink-Worker",
        )
        self.worker_thread.start()
    
    def __call__(self, record: dict) -> None:
        """Sink callable interface."""
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # Drop oldest message (simple overwrite protection)
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(record)
            except queue.Empty:
                pass
    
    def _worker_loop(self) -> None:
        """Background worker processing loop."""
        while True:
            try:
                # Get next item with timeout
                record = self.queue.get(timeout=self.batch_timeout)
                self.batch.append(record)
                
                # Process batch if full
                if len(self.batch) >= self.batch_size:
                    self._flush_batch()
            
            except queue.Empty:
                # Timeout - flush partial batch if data exists
                if self.batch:
                    elapsed = (datetime.now(timezone.utc).timestamp() - 
                              self.last_flush)
                    if elapsed >= self.batch_timeout:
                        self._flush_batch()
            
            except Exception as e:
                # Log error but continue
                logger.error(f"Error in AsyncQueueSink worker: {e}")
    
    def _flush_batch(self) -> None:
        """Process accumulated batch."""
        if not self.batch:
            return
        
        try:
            # Process batch (synchronously for now)
            # Could be made async with asyncio integration
            self.process_func(self.batch)
            self.batch.clear()
            self.last_flush = datetime.now(timezone.utc).timestamp()
        except Exception as e:
            logger.error(f"Error flushing AsyncQueueSink batch: {e}")
            # Keep batch for retry
    
    def flush(self) -> None:
        """Manually flush pending batch."""
        self._flush_batch()


# ============================================================================
# Supabase Sink
# ============================================================================

class SupabaseSink:
    """
    PostgreSQL/Supabase sink for audit logs and structured logging.
    
    Stores logs in a PostgreSQL table with:
    - Full record details (level, message, context)
    - Trace context (trace_id, span_id)
    - Query performance tracking
    - Automatic timestamp and circuit breaker protection
    
    Table schema:
        CREATE TABLE logs (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ DEFAULT NOW(),
            level VARCHAR(20),
            logger VARCHAR(256),
            message TEXT,
            module VARCHAR(256),
            function VARCHAR(256),
            line INTEGER,
            trace_id VARCHAR(32),
            span_id VARCHAR(16),
            extra JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """
    
    def __init__(
        self,
        supabase_client,
        table_name: str = "logs",
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        """
        Args:
            supabase_client: Supabase client instance (from supabase-py)
            table_name: PostgreSQL table name
            circuit_breaker: CircuitBreaker instance for failure handling
        """
        self.supabase = supabase_client
        self.table_name = table_name
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name="SupabaseSink")
    
    def __call__(self, record: dict) -> None:
        """Sink callable interface."""
        try:
            log_entry = self._format_record(record)
            
            def insert():
                self.supabase.table(self.table_name).insert(log_entry).execute()
            
            self.circuit_breaker.call(insert)
        
        except CircuitBreakerOpen:
            # Circuit is open, skip insert
            pass
        except Exception as e:
            # Don't crash logger on sink error
            logger.error(f"Error writing to SupabaseSink: {e}")
    
    @staticmethod
    def _format_record(record: dict) -> dict:
        """Format loguru record for database insert."""
        extra = record.get("extra", {})
        
        return {
            "level": record["level"].name,
            "logger": record["name"],
            "message": record["message"],
            "module": record["module"],
            "function": record["function"],
            "line": record["line"],
            "trace_id": extra.get("trace_id"),
            "span_id": extra.get("span_id"),
            "parent_span_id": extra.get("parent_span_id"),
            "extra": json.dumps({
                k: v for k, v in extra.items()
                if k not in ("trace_id", "span_id", "parent_span_id")
            }),
            "process_id": record["process"].id,
            "thread_id": record["thread"].id,
            "timestamp": record["time"].isoformat(),
        }


# ============================================================================
# Error Tracking Sink
# ============================================================================

@dataclass
class ErrorEvent:
    """Structured error event for tracking."""
    timestamp: str
    level: str
    logger: str
    message: str
    exception_type: Optional[str]
    exception_value: Optional[str]
    traceback: Optional[str]
    trace_id: Optional[str]
    span_id: Optional[str]
    context: dict


class ErrorTrackingSink:
    """
    Sink for error/exception tracking.
    
    Can integrate with:
    - Sentry
    - Rollbar
    - Datadog
    - Custom error tracking service
    
    Only processes ERROR and CRITICAL level logs.
    """
    
    def __init__(
        self,
        on_error: Callable[[ErrorEvent], None],
        track_warnings: bool = False,
    ):
        """
        Args:
            on_error: Callback function to handle error events
            track_warnings: Also track WARNING level logs
        """
        self.on_error = on_error
        self.track_warnings = track_warnings
        self.min_level = 30 if track_warnings else 40  # WARNING=30, ERROR=40
    
    def __call__(self, record: dict) -> None:
        """Sink callable interface."""
        # Filter by level
        if record["level"].no < self.min_level:
            return
        
        # Extract exception info
        exception_type = None
        exception_value = None
        traceback_str = None
        
        if record["exception"]:
            exception_type = record["exception"].type.__name__
            exception_value = str(record["exception"].value)
            traceback_str = "".join(
                traceback.format_exception(
                    record["exception"].type,
                    record["exception"].value,
                    record["exception"].traceback,
                )
            )
        
        # Build error event
        extra = record.get("extra", {})
        error_event = ErrorEvent(
            timestamp=record["time"].isoformat(),
            level=record["level"].name,
            logger=record["name"],
            message=record["message"],
            exception_type=exception_type,
            exception_value=exception_value,
            traceback=traceback_str,
            trace_id=extra.get("trace_id"),
            span_id=extra.get("span_id"),
            context={
                k: v for k, v in extra.items()
                if k not in ("trace_id", "span_id")
            },
        )
        
        try:
            self.on_error(error_event)
        except Exception as e:
            logger.error(f"Error in ErrorTrackingSink: {e}")


# ============================================================================
# Performance Monitoring Sink
# ============================================================================

class PerformanceMetricsSink:
    """
    Sink for collecting performance metrics.
    
    Tracks:
    - Operation durations
    - Slow operations
    - Performance trends
    """
    
    def __init__(
        self,
        on_metric: Callable[[dict], None],
        slow_threshold_ms: float = 1000.0,
    ):
        """
        Args:
            on_metric: Callback for metric events
            slow_threshold_ms: Threshold for "slow" operation
        """
        self.on_metric = on_metric
        self.slow_threshold_ms = slow_threshold_ms
    
    def __call__(self, record: dict) -> None:
        """Sink callable interface."""
        extra = record.get("extra", {})
        
        # Look for duration_ms field
        if "duration_ms" not in extra:
            return
        
        duration_ms = extra["duration_ms"]
        
        metric = {
            "timestamp": record["time"].isoformat(),
            "operation": extra.get("operation", "unknown"),
            "duration_ms": duration_ms,
            "is_slow": duration_ms > self.slow_threshold_ms,
            "trace_id": extra.get("trace_id"),
            "status": extra.get("status", "unknown"),
        }
        
        try:
            self.on_metric(metric)
        except Exception as e:
            logger.error(f"Error in PerformanceMetricsSink: {e}")
