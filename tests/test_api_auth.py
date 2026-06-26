from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from netengine.api.auth import require_auth
from netengine.core.state import RuntimeState


class _PostContext:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body or {"active": True, "sub": "user-1"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._body


class _ClientSession:
    def __init__(self, capture):
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.capture["url"] = url
        self.capture.update(kwargs)
        return _PostContext()


@pytest.mark.asyncio
async def test_require_auth_introspection_uses_platform_api_client_secret(monkeypatch):
    state = RuntimeState(
        phase_completed={"4": True},
        identity_platform_output={"platform_client_id": "uuid"},
        platform_client_id="uuid",
        platform_client_auth_id="platform-api",
        platform_client_secret="stored-secret",
    )
    capture = {}
    request = SimpleNamespace(headers={}, url=SimpleNamespace(path="/api/v1/world"))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bearer-token")

    monkeypatch.setenv("KEYCLOAK_PLATFORM_CLIENT_ID", "ignored-env-client")
    with (
        patch("netengine.api.auth.RuntimeState.load", return_value=state),
        patch("netengine.api.auth.aiohttp.ClientSession", return_value=_ClientSession(capture)),
    ):
        data = await require_auth(request, credentials)

    assert data == {"active": True, "sub": "user-1"}
    assert capture["data"] == {"token": "bearer-token", "client_id": "platform-api"}
    assert capture["auth"].login == "platform-api"
    assert capture["auth"].password == "stored-secret"


@pytest.mark.asyncio
async def test_require_auth_fails_when_platform_client_secret_missing(monkeypatch):
    state = RuntimeState(
        phase_completed={"4": True},
        identity_platform_output={"platform_client_id": "uuid"},
        platform_client_id="uuid",
        platform_client_auth_id="platform-api",
        platform_client_secret=None,
    )
    request = SimpleNamespace(headers={}, url=SimpleNamespace(path="/api/v1/world"))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bearer-token")

    monkeypatch.delenv("KEYCLOAK_PLATFORM_CLIENT_SECRET", raising=False)
    with patch("netengine.api.auth.RuntimeState.load", return_value=state):
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(request, credentials)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Platform API client secret not configured"
