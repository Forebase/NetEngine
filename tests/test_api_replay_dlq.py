"""API tests for DLQ replay route queue validation and mapping."""

from __future__ import annotations

from fastapi.testclient import TestClient

from netengine.api.app import app
from netengine.api.auth import require_auth
from netengine.events.queues import DLQ_BY_PRIMARY, Queue


class _FakePGMQClient:
    instances: list["_FakePGMQClient"] = []

    def __init__(self) -> None:
        self.received_from: list[Queue] = []
        type(self).instances.append(self)

    async def receive(self, queue: Queue, timeout: int = 1):
        self.received_from.append(queue)
        return None

    async def send(self, queue: Queue, envelope) -> None:
        raise AssertionError("send should not be called when the DLQ is empty")

    async def delete(self, queue: Queue, msg_id: int) -> None:
        raise AssertionError("delete should not be called when the DLQ is empty")


def _client(monkeypatch) -> TestClient:
    async def operator_user():
        return {"sub": "operator"}

    app.dependency_overrides[require_auth] = operator_user
    monkeypatch.setattr("netengine.core.pgmq_client.PGMQClient", _FakePGMQClient)
    _FakePGMQClient.instances.clear()
    return TestClient(app)


def test_replay_accepts_known_primary_queue(monkeypatch) -> None:
    client = _client(monkeypatch)
    try:
        response = client.post(
            f"/api/v1/queues/{Queue.DNS_UPDATES.value}/dlq/replay",
            headers={"Authorization": "Bearer token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"replayed": 0, "errors": []}
    assert len(_FakePGMQClient.instances) == 1


def test_replay_rejects_unknown_queue(monkeypatch) -> None:
    client = _client(monkeypatch)
    try:
        response = client.post(
            "/api/v1/queues/not_a_primary_queue/dlq/replay",
            headers={"Authorization": "Bearer token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown queue: not_a_primary_queue"
    assert _FakePGMQClient.instances == []


def test_replay_uses_declared_dlq_mapping(monkeypatch) -> None:
    declared_dlq = Queue.PHASE_EVENTS_DLQ
    monkeypatch.setitem(DLQ_BY_PRIMARY, Queue.DNS_UPDATES, declared_dlq)
    client = _client(monkeypatch)
    try:
        response = client.post(
            f"/api/v1/queues/{Queue.DNS_UPDATES.value}/dlq/replay",
            headers={"Authorization": "Bearer token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert _FakePGMQClient.instances[0].received_from == [declared_dlq]
