"""Authentication dependency for the NetEngine operator API.

Pre-Phase 4: validates X-Bootstrap-Secret header against the local env secret.
Post-Phase 4: validates OIDC bearer token via Keycloak introspection.
"""

from __future__ import annotations

import os

import aiohttp
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from netengine.core.state import RuntimeState

KEYCLOAK_ISSUER = os.environ.get(
    "KEYCLOAK_PLATFORM_ISSUER",
    "https://auth.platform.internal/realms/platform",
)

_bearer = HTTPBearer(auto_error=False)


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
    client_id = (
        getattr(state, "platform_client_auth_id", None)
        or os.environ.get("KEYCLOAK_PLATFORM_CLIENT_ID")
        or "platform-api"
    )
    client_secret = getattr(state, "platform_client_secret", None) or os.environ.get(
        "KEYCLOAK_PLATFORM_CLIENT_SECRET", ""
    )
    if not client_secret:
        raise HTTPException(status_code=500, detail="Platform API client secret not configured")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token/introspect",
                data={"token": token, "client_id": client_id},
                auth=aiohttp.BasicAuth(client_id, client_secret),
                ssl=False,
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
