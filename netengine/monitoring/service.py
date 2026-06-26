"""Monitoring service — runs all probes on schedule and publishes world_health events to pgmq."""

import asyncio
import logging

from netengine.core.pgmq_client import PGMQClient
from netengine.diagnostic import DiagnosticRunner, ProbeResult, ProbeStatus, build_runner
from netengine.events.schema import EventEnvelope
from netengine.spec.models import NetEngineSpec

logger = logging.getLogger(__name__)


class MonitoringService:
    """Always-running monitoring service.

    Runs all diagnostic probes on a configurable schedule and publishes
    aggregated world_health events to pgmq. Other consumers (alerting,
    dashboards, webhooks) can subscribe to these events for reactive
    monitoring.
    """

    def __init__(self, spec: NetEngineSpec, interval_seconds: float = 60.0) -> None:
        """Initialize monitoring service.

        Args:
            spec: NetEngine world specification
            interval_seconds: Probe run interval (default: 60 seconds)
        """
        self._spec = spec
        self._interval_seconds = interval_seconds
        self._runner = build_runner(spec)
        self._pgmq = PGMQClient()
        self._running = False

    async def start(self) -> None:
        """Start the monitoring service (background consumer loop)."""
        self._running = True
        logger.info(
            f"Monitoring service started (interval: {self._interval_seconds}s, "
            f"probes: {len(self._runner._probes)})"
        )

        while self._running:
            try:
                await self._run_probe_cycle()
            except asyncio.CancelledError:
                logger.info("Monitoring service cancelled")
                break
            except Exception as e:
                logger.error(f"Monitoring cycle failed: {e}", exc_info=True)

            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                logger.info("Monitoring service sleep cancelled")
                break

    async def stop(self) -> None:
        """Stop the monitoring service."""
        self._running = False
        logger.info("Monitoring service stopped")

    async def _run_probe_cycle(self) -> None:
        """Run all probes and publish world_health event."""
        logger.debug("Starting probe cycle")

        results = await self._runner.run()

        summary = self._summarize_results(results)

        event = EventEnvelope.create(
            event_type="monitoring.world_health",
            emitted_by="monitoring_service",
            payload={
                "world_name": self._spec.metadata.name,
                "status": summary["status"],
                "total_probes": len(results),
                "passed": summary["passed"],
                "warned": summary["warned"],
                "failed": summary["failed"],
                "skipped": summary["skipped"],
                "probes": [self._probe_result_to_dict(r) for r in results],
                "summary": summary["message"],
            },
        )

        try:
            msg_id = await self._pgmq.send("world_health", event)
            logger.debug(f"Published world_health event (msg_id: {msg_id})")
        except Exception as e:
            logger.error(f"Failed to publish world_health event: {e}", exc_info=True)

    def _summarize_results(self, results: list[ProbeResult]) -> dict:
        """Summarize probe results into status and message."""
        passed = sum(1 for r in results if r.status == ProbeStatus.OK)
        warned = sum(1 for r in results if r.status == ProbeStatus.WARN)
        failed = sum(1 for r in results if r.status == ProbeStatus.FAIL)
        skipped = sum(1 for r in results if r.status == ProbeStatus.SKIP)

        # Determine overall status
        if failed > 0:
            status = "critical"
            message = f"{failed} probe(s) failed"
        elif warned > 0:
            status = "warning"
            message = f"{warned} probe(s) warned"
        else:
            status = "healthy"
            message = f"All {passed} probes passed"

        return {
            "status": status,
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "skipped": skipped,
            "message": message,
        }

    @staticmethod
    def _probe_result_to_dict(result: ProbeResult) -> dict:
        """Convert ProbeResult to dict for event payload."""
        return {
            "name": result.name,
            "status": result.status.value,
            "detail": result.detail,
            "hint": result.hint,
            "elapsed_ms": result.elapsed_ms,
        }
