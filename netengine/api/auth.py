"""Authentication dependency for the NetEngine operator API.

Pre-Phase 4: validates X-Bootstrap-Secret header against the local env secret.
Post-Phase 4: validates OIDC bearer token via Keycloak introspection.
"""

from __future__ import annotations

import os
import ssl
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import aiohttp
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from netengine.core.state import RuntimeState

KEYCLOAK_ISSUER = os.environ.get(
    "KEYCLOAK_PLATFORM_ISSUER",
    "https://auth.platform.internal/realms/platform",
)
INSECURE_TLS_ENV = "NETENGINE_INSECURE_TLS"
CA_BUNDLE_ENV = "NETENGINE_CA_BUNDLE"

_bearer = HTTPBearer(auto_error=False)


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


@contextmanager
def _keycloak_ssl_context(state: RuntimeState) -> Iterator[ssl.SSLContext | bool | None]:
    """Yield the TLS option for aiohttp Keycloak calls.

    The normal path returns ``None`` so aiohttp uses Python's default certificate
    verification. If NetEngine is running against its own self-signed platform CA,
    callers can either configure a CA bundle path or rely on the CA PEM persisted in
    runtime state. ``ssl=False`` is reserved for the explicit development-only escape
    hatch.
    """
    if _is_truthy(os.environ.get(INSECURE_TLS_ENV)):
        yield False
        return

    ca_bundle = os.environ.get(CA_BUNDLE_ENV)
    if ca_bundle:
        yield ssl.create_default_context(cafile=ca_bundle)
        return

    ca_cert_pem = getattr(state, "ca_cert_pem", None)
    if ca_cert_pem:
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=True) as ca_file:
            ca_file.write(ca_cert_pem)
            ca_file.flush()
            yield ssl.create_default_context(cafile=ca_file.name)
        return

    default_bundle = Path("runtime") / "ca-bundle.pem"
    if default_bundle.exists():
        yield ssl.create_default_context(cafile=str(default_bundle))
        return

    yield None


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI dependency.  Returns a minimal user dict on success."""
    bootstrap_secret = os.environ.get("NETENGINES_BOOTSTRAP_SECRET", "")
    state = RuntimeState.load()
    phase4_done = state.phase_completed.get("4", False)

    if not phase4_done:
        # Bootstrap phase: accept secret in X-Bootstrap-Secret header
        secret = request.headers.get("X-Bootstrap-Secret", "")
        if bootstrap_secret and secret == bootstrap_secret:
            return {"sub": "bootstrap", "roles": ["admin"]}
        # Also allow an unauthenticated health check
        if request.url.path.endswith("/health"):
            return {"sub": "anon"}
        raise HTTPException(
            status_code=401, detail="Bootstrap secret required (Phase 4 not yet complete)"
        )

    if not credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")

    token = credentials.credentials
    admin_password = getattr(state, "bootstrap_admin_password", None) or os.environ.get(
        "KEYCLOAK_ADMIN_PASSWORD", ""
    )
    if not admin_password:
        raise HTTPException(status_code=500, detail="Keycloak admin credentials not configured")

    try:
        async with aiohttp.ClientSession() as session:
            with _keycloak_ssl_context(state) as ssl_context:
                async with session.post(
                    f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token/introspect",
                    data={"token": token},
                    auth=aiohttp.BasicAuth("admin-cli", admin_password),
                    ssl=ssl_context,
                ) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=401, detail="Token introspection failed")
                    data = await resp.json()
                    if not data.get("active"):
                        raise HTTPException(status_code=401, detail="Token expired or inactive")
                    return data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Auth service unavailable: {exc}")
