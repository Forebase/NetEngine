import ssl
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from netengine.api import auth
from netengine.core.state import RuntimeState


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {"active": True, "sub": "user-1"}


class _Session:
    post_kwargs = None

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
    def post(self, *args, **kwargs):
        type(self).post_kwargs = kwargs
        return _Response()


def _phase4_state(**overrides):
    values = {
        "phase_completed": {"4": True},
        "bootstrap_admin_password": "admin-password",
    }
    values.update(overrides)
    return RuntimeState(**values)


async def _call_require_auth(monkeypatch, state):
    _Session.post_kwargs = None
    monkeypatch.setattr(auth.RuntimeState, "load", classmethod(lambda cls: state))
    monkeypatch.setattr(auth.aiohttp, "ClientSession", _Session)
    request = SimpleNamespace(headers={}, url=SimpleNamespace(path="/world"))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token-1")

    result = await auth.require_auth(request, credentials)

    assert result["active"] is True
    assert _Session.post_kwargs is not None
    return _Session.post_kwargs["ssl"]


@pytest.mark.asyncio
async def test_require_auth_uses_default_tls_verification(monkeypatch):
    monkeypatch.delenv(auth.INSECURE_TLS_ENV, raising=False)
    monkeypatch.delenv(auth.CA_BUNDLE_ENV, raising=False)

    ssl_option = await _call_require_auth(monkeypatch, _phase4_state())

    assert ssl_option is None


@pytest.mark.asyncio
async def test_require_auth_insecure_tls_override_is_opt_in(monkeypatch):
    monkeypatch.setenv(auth.INSECURE_TLS_ENV, "true")
    monkeypatch.delenv(auth.CA_BUNDLE_ENV, raising=False)

    ssl_option = await _call_require_auth(monkeypatch, _phase4_state())

    assert ssl_option is False


@pytest.mark.asyncio
async def test_require_auth_uses_configured_ca_bundle(monkeypatch, tmp_path):
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("certificate-placeholder")
    monkeypatch.delenv(auth.INSECURE_TLS_ENV, raising=False)
    monkeypatch.setenv(auth.CA_BUNDLE_ENV, str(ca_bundle))

    captured_cafile = None

    def fake_create_default_context(*, cafile=None):
        nonlocal captured_cafile
        captured_cafile = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(auth.ssl, "create_default_context", fake_create_default_context)

    ssl_option = await _call_require_auth(monkeypatch, _phase4_state())

    assert isinstance(ssl_option, ssl.SSLContext)
    assert captured_cafile == str(ca_bundle)


@pytest.mark.asyncio
async def test_require_auth_uses_persisted_runtime_ca_bundle(monkeypatch):
    monkeypatch.delenv(auth.INSECURE_TLS_ENV, raising=False)
    monkeypatch.delenv(auth.CA_BUNDLE_ENV, raising=False)

    captured_cafile = None

    def fake_create_default_context(*, cafile=None):
        nonlocal captured_cafile
        captured_cafile = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(auth.ssl, "create_default_context", fake_create_default_context)

    ssl_option = await _call_require_auth(
        monkeypatch,
        _phase4_state(ca_cert_pem="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"),
    )

    assert isinstance(ssl_option, ssl.SSLContext)
    assert captured_cafile is not None
    assert captured_cafile.endswith(".pem")
