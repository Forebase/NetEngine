import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.consumer_supervisor import ConsumerSupervisor
from netengine.events.queues import Queue, dlq_for
from netengine.events.schema import EventEnvelope
from netengine.phases.phase_services import ServicesPhaseHandler
from netengine.workers.dlq_worker import DLQReplayWorker


@pytest.mark.asyncio
async def test_consumer_supervisor_reports_crash_and_graceful_shutdown(monkeypatch):
    supervisor = ConsumerSupervisor()
    first_run = asyncio.Event()

    async def crashing_worker():
        first_run.set()
        raise RuntimeError("boom")

    async def long_worker():
        await asyncio.Event().wait()

    supervisor.register("worker.crashes", crashing_worker)
    supervisor.register("worker.long", long_worker)

    await supervisor.start_all()
    await asyncio.wait_for(first_run.wait(), timeout=1)
    await asyncio.sleep(0)

    crashed = supervisor.get_structured_status()["worker.crashes"]
    assert crashed["state"] == "failed"
    assert crashed["last_error"] == "boom"
    assert crashed["restarts"] == 1

    await supervisor.stop_all()
    stopped = supervisor.get_structured_status()["worker.long"]
    assert stopped["state"] == "stopped"


@pytest.mark.asyncio
async def test_services_workers_register_disabled_when_pgmq_unavailable(minimal_spec):
    context = MagicMock()
    context.spec = minimal_spec
    context.runtime_state = MagicMock()
    context.runtime_state.substrate_output = {"ok": True}
    context.runtime_state.dns_output = {"ok": True}
    context.runtime_state.pki_output = {"ok": True}
    context.runtime_state.identity_platform_output = {"ok": True}
    context.runtime_state.world_registry_output = {"ok": True}
    context.runtime_state.identity_inworld_output = {"ok": True}
    context.runtime_state.ands_output = {"ok": True}
    context.consumer_supervisor = ConsumerSupervisor()
    context.pgmq_client = None
    context.logger = MagicMock()

    with patch("netengine.phases.phase_services.DockerHandler"), patch(
        "netengine.phases.phase_services.DNSHandler"
    ), patch("netengine.phases.phase_services.PKIHandler"), patch(
        "netengine.phases.phase_services.MailHandler"
    ) as mail_cls, patch("netengine.phases.phase_services.StorageHandler") as storage_cls:
        mail_cls.return_value.deploy_postfix = AsyncMock(return_value={"container_id": "mail"})
        storage_cls.return_value.deploy_minio = AsyncMock(return_value={"container_id": "minio"})
        await ServicesPhaseHandler().execute(context)

    status = context.consumer_supervisor.get_structured_status()
    assert status["services.org_admission"]["state"] == "disabled"
    assert status["dlq.services_admissions"]["state"] == "disabled"
    assert status["monitoring.world_health"]["state"] == "disabled"


@pytest.mark.asyncio
async def test_dlq_replay_requires_resolved_marker():
    pgmq = AsyncMock()
    unresolved = EventEnvelope.create(
        event_type="services.test", emitted_by="test", payload={"dlq_reason": "still broken"}
    )
    pgmq.receive.return_value = {"msg_id": 7, "message": json.dumps(unresolved.to_dict())}

    worker = DLQReplayWorker(pgmq, Queue.SERVICES_ADMISSIONS, dlq_for(Queue.SERVICES_ADMISSIONS))

    assert await worker.replay_once() is False
    pgmq.send.assert_not_called()
    pgmq.delete.assert_not_called()


@pytest.mark.asyncio
async def test_dlq_replay_moves_resolved_message_back_to_primary():
    pgmq = AsyncMock()
    resolved = EventEnvelope.create(
        event_type="services.test",
        emitted_by="test",
        payload={"dlq_reason": "fixed", "dlq_resolved": True, "org": "acme"},
    )
    pgmq.receive.return_value = {"msg_id": 8, "message": json.dumps(resolved.to_dict())}

    worker = DLQReplayWorker(pgmq, Queue.SERVICES_ADMISSIONS, dlq_for(Queue.SERVICES_ADMISSIONS))

    assert await worker.replay_once() is True
    sent_queue, sent_event = pgmq.send.call_args.args
    assert sent_queue == Queue.SERVICES_ADMISSIONS
    assert sent_event.retry_count == 0
    assert sent_event.payload == {"org": "acme"}
    pgmq.delete.assert_awaited_once_with(dlq_for(Queue.SERVICES_ADMISSIONS), 8)
