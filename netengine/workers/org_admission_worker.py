"""Worker for org admission events queue."""

from loguru import logger

from netengine.events.schema import EventEnvelope
from netengine.workers.base import QueueWorker


class OrgAdmissionWorker(QueueWorker):
    """Consumes org admission events and provisions org-specific infrastructure."""

    def __init__(self, queue_name: str) -> None:
        self._queue_name = queue_name

    @property
    def name(self) -> str:
        return f"org_admission_worker_{self._queue_name}"

    @property
    def queue_name(self) -> str:
        return self._queue_name

    async def handle_message(self, message: EventEnvelope) -> None:
        """Process org admission event.

        Expected payload:
        {
            "org_id": "org_123",
            "org_name": "acme-corp",
            "admin_email": "admin@acme.corp",
        }
        """
        payload = message.payload
        org_id = payload.get("org_id")
        org_name = payload.get("org_name")
        admin_email = payload.get("admin_email")

        if not all([org_id, org_name, admin_email]):
            raise ValueError(f"Invalid org admission payload: {payload}")

        logger.info(
            f"Org admission: {org_name} (id={org_id}) to {self.queue_name}"
        )

        # TODO: Provision org-specific infrastructure
        # - Create Keycloak realm (for inworld_admissions)
        # - Create AND instance (for and_admissions)
        # - Create service namespaces (for services_admissions)
        # For now, just log the event
