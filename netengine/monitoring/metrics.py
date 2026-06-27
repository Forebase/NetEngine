"""Prometheus metrics for NetEngine orchestration.

Metrics are registered on a dedicated CollectorRegistry (not the global default)
to prevent leakage in test environments where the module is imported multiple times.

Usage:
    from netengine.monitoring.metrics import record_phase
    async with record_phase(phase_num):
        await handler.execute(context)
"""

import time
from contextlib import contextmanager
from typing import Generator

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

phase_duration_seconds: Histogram = Histogram(
    "netengine_phase_duration_seconds",
    "Wall-clock time spent executing each bootstrap phase",
    labelnames=["phase_num"],
    registry=REGISTRY,
)

phase_errors_total: Counter = Counter(
    "netengine_phase_errors_total",
    "Total number of phase execution failures",
    labelnames=["phase_num"],
    registry=REGISTRY,
)

healthcheck_failures_total: Counter = Counter(
    "netengine_healthcheck_failures_total",
    "Total number of post-execute healthcheck failures",
    labelnames=["phase_num"],
    registry=REGISTRY,
)

dlq_depth: Gauge = Gauge(
    "netengine_dlq_depth",
    "Current number of messages waiting in each dead-letter queue",
    labelnames=["queue_name"],
    registry=REGISTRY,
)


@contextmanager
def record_phase(phase_num: int) -> Generator[None, None, None]:
    """Context manager: time a phase and increment the error counter on exception."""
    start = time.perf_counter()
    try:
        yield
        phase_duration_seconds.labels(phase_num=str(phase_num)).observe(time.perf_counter() - start)
    except Exception:
        phase_errors_total.labels(phase_num=str(phase_num)).inc()
        raise


def record_healthcheck_failure(phase_num: int) -> None:
    """Increment the healthcheck failure counter for a phase."""
    healthcheck_failures_total.labels(phase_num=str(phase_num)).inc()


def set_dlq_depth(queue_name: str, depth: int) -> None:
    """Update the DLQ depth gauge for a named queue."""
    dlq_depth.labels(queue_name=queue_name).set(depth)
