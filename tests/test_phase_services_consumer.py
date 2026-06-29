"""Tests for Phase 8 org-admission service provisioning consumer."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from netengine.events.factory import org_admitted
from netengine.events.queues import Queue
from netengine.events.schema import EventEnvelope
from netengine.phases.phase_services import ServicesPhaseHandler


async def _stop_consumer(*_args, **_kwargs):
    raise asyncio.CancelledError


def _message(envelope: EventEnvelope, msg_id: int = 42) -> dict:
    return {"msg_id": msg_id, "message": json.dumps(envelope.to_dict())}


async def test_services_consumer_provisions_and_deletes_on_success(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    event = org_admitted(org_name="acme", capabilities=[], and_profile="business")
    event.payload["service_config"] = {
        "mail": {"enabled": True},
        "storage": {"enabled": True},
    }
    context.pgmq_client.receive = AsyncMock(side_effect=[_message(event), None])

    provision = AsyncMock(return_value={"org_name": "acme", "mail": {}, "storage": {}})
    persist = AsyncMock()
    monkeypatch.setattr(handler, "_provision_org_services", provision)
    monkeypatch.setattr(handler, "_persist_org_service_provisioning", persist)
    monkeypatch.setattr(asyncio, "sleep", _stop_consumer)

    with pytest.raises(asyncio.CancelledError):
        await handler._consume_org_admission_events(context, AsyncMock(), AsyncMock())

    provision.assert_awaited_once()
    persist.assert_awaited_once()
    context.pgmq_client.delete.assert_awaited_once_with(Queue.SERVICES_ADMISSIONS, 42)
    context.pgmq_client.archive_to_dlq.assert_not_awaited()


async def test_services_consumer_ignores_non_admission_events(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    event = EventEnvelope.create("org.updated", "test", {"org_name": "acme"})
    context.pgmq_client.receive = AsyncMock(side_effect=[_message(event, 7), None])
    provision = AsyncMock()
    monkeypatch.setattr(handler, "_provision_org_services", provision)
    monkeypatch.setattr(asyncio, "sleep", _stop_consumer)

    with pytest.raises(asyncio.CancelledError):
        await handler._consume_org_admission_events(context, AsyncMock(), AsyncMock())

    provision.assert_not_awaited()
    context.pgmq_client.delete.assert_awaited_once_with(Queue.SERVICES_ADMISSIONS, 7)
    context.pgmq_client.archive_to_dlq.assert_not_awaited()


async def test_services_consumer_archives_malformed_messages(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    context.pgmq_client.receive = AsyncMock(
        side_effect=[{"msg_id": 99, "message": "not-json"}, None]
    )
    monkeypatch.setattr(asyncio, "sleep", _stop_consumer)

    with pytest.raises(asyncio.CancelledError):
        await handler._consume_org_admission_events(context, AsyncMock(), AsyncMock())

    context.pgmq_client.delete.assert_not_awaited()
    context.pgmq_client.archive_to_dlq.assert_awaited_once()
    assert (
        context.pgmq_client.archive_to_dlq.await_args.args[0]
        == Queue.SERVICES_ADMISSIONS
    )
    assert context.pgmq_client.archive_to_dlq.await_args.args[1] == 99


async def test_services_consumer_archives_provisioning_failure(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    event = org_admitted(org_name="acme", capabilities=[], and_profile="business")
    context.pgmq_client.receive = AsyncMock(side_effect=[_message(event, 12), None])
    monkeypatch.setattr(
        handler, "_provision_org_services", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(asyncio, "sleep", _stop_consumer)

    with pytest.raises(asyncio.CancelledError):
        await handler._consume_org_admission_events(context, AsyncMock(), AsyncMock())

    context.pgmq_client.delete.assert_not_awaited()
    context.pgmq_client.archive_to_dlq.assert_awaited_once_with(
        Queue.SERVICES_ADMISSIONS, 12, "boom"
    )


async def test_services_consumer_continues_when_dlq_archiving_fails(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    event = org_admitted(org_name="acme", capabilities=[], and_profile="business")
    context.pgmq_client.receive = AsyncMock(side_effect=[_message(event, 13), None])
    context.pgmq_client.archive_to_dlq = AsyncMock(side_effect=RuntimeError("dlq down"))
    monkeypatch.setattr(
        handler, "_provision_org_services", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(asyncio, "sleep", _stop_consumer)

    with pytest.raises(asyncio.CancelledError):
        await handler._consume_org_admission_events(context, AsyncMock(), AsyncMock())

    context.pgmq_client.archive_to_dlq.assert_awaited_once_with(
        Queue.SERVICES_ADMISSIONS, 13, "boom"
    )


async def test_provision_org_services_calls_mail_storage_handlers(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    dns = AsyncMock()

    monkeypatch.setattr(
        "netengine.phases.phase_services.MailHandler._generate_dkim_keys",
        AsyncMock(return_value=("private", "public")),
    )
    monkeypatch.setattr(
        "netengine.phases.phase_services.PKIHandler.issue_cert",
        AsyncMock(return_value=("cert", "key")),
    )
    monkeypatch.setattr(
        "netengine.phases.phase_services.PKIHandler.extract_cert_expiry",
        lambda _self, _cert: datetime.now(UTC) + timedelta(days=30),
    )
    monkeypatch.setattr(
        "netengine.phases.phase_services.StorageHandler._create_bucket",
        AsyncMock(),
    )

    result = await handler._provision_org_services(
        context,
        AsyncMock(),
        dns,
        "acme",
        {"mail": {"enabled": True}, "storage": {"enabled": True}},
    )

    assert result["mail"]["domain"] == "acme.internal"
    assert result["storage"]["bucket"] == "acme-data"
    assert "storage-acme.platform.internal" in context.runtime_state.issued_certificates
    assert dns.add_zone_record.await_count >= 5


async def test_persist_org_service_provisioning_updates_runtime_and_db(
    phase_context_with_mocks, monkeypatch
):
    context = phase_context_with_mocks
    handler = ServicesPhaseHandler()
    calls = []

    class Query:
        def __init__(self, table):
            self.table = table

        def upsert(self, data):
            calls.append((self.table, data))
            return self

        async def execute(self):
            return SimpleNamespace(data=[])

    class DB:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr(
        "netengine.core.supabase_client.get_db", AsyncMock(return_value=DB())
    )

    await handler._persist_org_service_provisioning(
        context,
        "acme",
        {
            "org_name": "acme",
            "provisioned_at": "2026-01-01T00:00:00+00:00",
            "mail": {"domain": "acme.internal"},
            "storage": {"bucket": "acme-data"},
        },
    )

    assert (
        context.runtime_state.world_services_output["orgs"]["acme"]["storage"]["bucket"]
        == "acme-data"
    )
    assert [name for name, _ in calls] == [
        "mail_domains",
        "storage_buckets",
        "service_provisioning",
    ]
