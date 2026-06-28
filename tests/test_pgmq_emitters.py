from unittest.mock import AsyncMock, MagicMock

import pytest

from netengine.core.state import RuntimeState
from netengine.events.queues import Queue
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.substrate import SubstrateHandler
from netengine.logging import get_logger
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler


@pytest.fixture
def context_with_pgmq() -> PhaseContext:
    state = RuntimeState()
    state.correlation_id = "correlation-123"
    state.parent_event_id = "parent-456"
    context = PhaseContext(runtime_state=state, spec=MagicMock(), logger=get_logger("test"))
    context.pgmq_client = MagicMock()
    context.pgmq_client.send = AsyncMock()
    return context


@pytest.mark.parametrize(
    ("handler", "event_type", "payload", "expected_queue"),
    [
        (SubstrateHandler(), "substrate.initialized", {"healthy": True}, Queue.PHASE_EVENTS),
        (DNSHandler(), "dns.zones_ready", {"zones": ["root.internal"]}, Queue.DNS_UPDATES),
        (
            InWorldIdentityPhaseHandler(),
            "inworld_identity.ready",
            {"realms_created": ["acme"]},
            Queue.PHASE_EVENTS,
        ),
        (PKIPhaseHandler(), "pki.ready", {"ca_dns": "ca.internal"}, Queue.PHASE_EVENTS),
    ],
)
async def test_emitters_send_queue_name_and_event(
    context_with_pgmq: PhaseContext,
    handler,
    event_type: str,
    payload: dict,
    expected_queue: Queue,
) -> None:
    await handler._emit_event(context_with_pgmq, event_type=event_type, payload=payload)

    context_with_pgmq.pgmq_client.send.assert_awaited_once()
    queue_name, event = context_with_pgmq.pgmq_client.send.await_args.args
    assert queue_name == expected_queue
    assert event.event_type == event_type
    assert event.payload == payload
