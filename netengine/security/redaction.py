"""Secret redaction helpers for runtime state and support bundles."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

REDACTION_TEXT = "[REDACTED]"

_SECRET_FIELD_NAMES = {
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "private_key_pem",
    "key_pem",
    "tls_key",
    "client_secret",
}


def _is_secret_field(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return normalized in _SECRET_FIELD_NAMES or normalized.endswith(
        ("_secret", "_password", "_token")
    )


def _contains_private_pem(value: str) -> bool:
    return "-----BEGIN " in value and "PRIVATE KEY-----" in value


def _fallback_redact(value: Any, *, drop_secret_fields: bool) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, child in value.items():
            if _is_secret_field(str(key)):
                if not drop_secret_fields:
                    redacted[key] = REDACTION_TEXT
                continue
            redacted[key] = _fallback_redact(child, drop_secret_fields=drop_secret_fields)
        return redacted
    if isinstance(value, list):
        return [_fallback_redact(child, drop_secret_fields=drop_secret_fields) for child in value]
    if isinstance(value, str) and _contains_private_pem(value):
        return None if drop_secret_fields else REDACTION_TEXT
    return value


def _redactable_redact(value: Any, *, drop_secret_fields: bool) -> Any:
    """Use Sober-Co/redactable when installed, falling back to local rules.

    The redactable package is expected to be published to PyPI during alpha.  Its
    public API may vary while it is young, so this adapter recognizes a few likely
    entry points and preserves NetEngine's documented behavior if unavailable.
    """

    if importlib.util.find_spec("redactable") is None:
        return _fallback_redact(value, drop_secret_fields=drop_secret_fields)

    redactable = importlib.import_module("redactable")
    for attr in ("redact", "redact_secrets", "redact_value"):
        candidate = getattr(redactable, attr, None)
        if callable(candidate):
            redacted = candidate(value)
            if drop_secret_fields:
                return _fallback_redact(redacted, drop_secret_fields=True)
            return redacted

    Redactor = getattr(redactable, "Redactor", None)
    if Redactor is not None:
        redactor = Redactor()
        candidate = getattr(redactor, "redact", None)
        if callable(candidate):
            redacted = candidate(value)
            if drop_secret_fields:
                return _fallback_redact(redacted, drop_secret_fields=True)
            return redacted

    return _fallback_redact(value, drop_secret_fields=drop_secret_fields)


def redact_for_api(value: Any, *, include_secrets: bool = False) -> Any:
    """Return a runtime-state value safe for operator API responses."""

    if include_secrets:
        return value
    return _redactable_redact(value, drop_secret_fields=False)


def redact_for_support_bundle(value: Any) -> Any:
    """Return a runtime-state value safe for support bundles and import/export."""

    return _redactable_redact(value, drop_secret_fields=True)
