"""Tests for the NetEngine domain exception hierarchy."""

import pytest

from netengine.errors import (
    BaseNetEngineException,
    DNSError,
    GatewayError,
    IdentityError,
    PKIError,
    RegistryError,
    ServicesError,
    SubstrateError,
)


def test_base_exception_code_and_message():
    exc = BaseNetEngineException("something went wrong")
    assert "[NETENGINE]" in str(exc)
    assert "something went wrong" in str(exc)


def test_base_exception_default_message():
    exc = BaseNetEngineException()
    assert exc._message == BaseNetEngineException.default_message


def test_subclass_codes():
    assert SubstrateError.code == "SUBSTRATE"
    assert DNSError.code == "DNS"
    assert PKIError.code == "PKI"
    assert IdentityError.code == "IDENTITY"
    assert RegistryError.code == "REGISTRY"
    assert GatewayError.code == "GATEWAY"
    assert ServicesError.code == "SERVICES"


def test_context_kwargs_stored():
    exc = DNSError("zone missing", zone="platform.internal", available=["root.internal"])
    assert exc.context == {"zone": "platform.internal", "available": ["root.internal"]}


def test_str_includes_code():
    exc = PKIError("ca bootstrap failed")
    assert str(exc) == "[PKI] ca bootstrap failed"


def test_cause_chaining():
    original = ValueError("original")
    exc = DNSError("wrapped", cause=original)
    assert exc.__cause__ is original


def test_is_exception():
    with pytest.raises(DNSError):
        raise DNSError("test")


def test_subclass_of_base():
    exc = PKIError("test")
    assert isinstance(exc, BaseNetEngineException)
    assert isinstance(exc, Exception)


def test_log_on_init_does_not_raise_when_logging_unavailable(monkeypatch):
    """Logging failures must never mask the real exception."""
    import netengine.errors as errors_mod

    original_log_on_init = BaseNetEngineException.log_on_init

    class BrokenLogger:
        def bind(self, **_):
            raise RuntimeError("logger broken")

    def broken_get_logger(_):
        return BrokenLogger()

    # Patch the lazy import inside _log
    import sys
    import types

    fake_logging_mod = types.ModuleType("netengine.logging")
    fake_logging_mod.get_logger = broken_get_logger  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "netengine.logging", fake_logging_mod)

    # Should not raise despite broken logger
    exc = DNSError("test with broken logger")
    assert exc._message == "test with broken logger"


def test_supabase_client_missing_env(monkeypatch):
    """get_supabase raises a clear RuntimeError, not bare KeyError, when vars are unset."""
    import netengine.core.supabase_client as sc

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    sc._supabase = None  # reset cached singleton

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        sc.get_supabase()
