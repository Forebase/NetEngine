"""Always-running monitoring service that publishes world health events to pgmq."""

from netengine.monitoring.service import MonitoringService

__all__ = ["MonitoringService"]
