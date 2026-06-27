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
