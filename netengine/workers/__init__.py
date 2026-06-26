"""Queue workers and registry."""

from typing import Any, Dict, Type

from netengine.workers.base import QueueWorker
from netengine.workers.dns_updates_worker import DNSUpdatesWorker
from netengine.workers.org_admission_worker import OrgAdmissionWorker


class QueueWorkerRegistry:
    """Registry of all pgmq queue workers and metadata."""

    REGISTRY: Dict[str, Dict[str, Any]] = {
        "dns_updates": {
            "worker_class": DNSUpdatesWorker,
            "phase": 5,
            "dlq": "dns_updates_dlq",
            "description": "DNS zone update events",
        },
        "and_admissions": {
            "worker_class": OrgAdmissionWorker,
            "phase": 7,
            "dlq": "and_admissions_dlq",
            "queue_kwargs": {"queue_name": "and_admissions"},
            "description": "Org admission to AND infrastructure",
        },
        "inworld_admissions": {
            "worker_class": OrgAdmissionWorker,
            "phase": 6,
            "dlq": "inworld_admissions_dlq",
            "queue_kwargs": {"queue_name": "inworld_admissions"},
            "description": "Org admission to in-world identity",
        },
        "services_admissions": {
            "worker_class": OrgAdmissionWorker,
            "phase": 8,
            "dlq": "services_admissions_dlq",
            "queue_kwargs": {"queue_name": "services_admissions"},
            "description": "Org admission to services",
        },
    }

    @classmethod
    def get_worker(cls, queue_name: str) -> QueueWorker:
        """Instantiate a worker for the given queue."""
        if queue_name not in cls.REGISTRY:
            raise ValueError(f"Unknown queue: {queue_name}")

        entry = cls.REGISTRY[queue_name]
        worker_class = entry["worker_class"]
        queue_kwargs = entry.get("queue_kwargs", {})

        return worker_class(**queue_kwargs)

    @classmethod
    def queue_names(cls) -> list[str]:
        """Get all queue names."""
        return list(cls.REGISTRY.keys())

    @classmethod
    def queues_for_phase(cls, phase: int) -> list[str]:
        """Get queues consumed by a specific phase."""
        return [
            name
            for name, entry in cls.REGISTRY.items()
            if entry.get("phase") == phase
        ]


__all__ = [
    "QueueWorker",
    "DNSUpdatesWorker",
    "OrgAdmissionWorker",
    "QueueWorkerRegistry",
]
