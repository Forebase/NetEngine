"""Worker for DNS updates queue."""

from loguru import logger

from netengine.events.schema import EventEnvelope
from netengine.workers.base import QueueWorker


class DNSUpdatesWorker(QueueWorker):
    """Consumes DNS update events and propagates to zone files."""

    @property
    def name(self) -> str:
        return "dns_updates_worker"

    @property
    def queue_name(self) -> str:
        return "dns_updates"

    async def handle_message(self, message: EventEnvelope) -> None:
        """Process DNS update event.

        Expected payload:
        {
            "zone": "platform.internal",
            "record_name": "keycloak",
            "record_type": "A",
            "record_value": "10.0.0.7",
        }
        """
        payload = message.payload
        zone = payload.get("zone")
        record_name = payload.get("record_name")
        record_type = payload.get("record_type", "A")
        record_value = payload.get("record_value")

        if not all([zone, record_name, record_value]):
            raise ValueError(f"Invalid DNS update payload: {payload}")

        logger.info(
            f"DNS update: {record_name}.{zone} ({record_type}) -> {record_value}"
        )

        # TODO: Trigger DNS zone file update
        # This would integrate with the DNS handler to add/update records
        # For now, just log the event
