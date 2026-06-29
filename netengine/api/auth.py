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
from netengine.logs import get_logger

logger = get_logger(__name__)

KEYCLOAK_ISSUER = os.environ.get(
    "KEYCLOAK_PLATFORM_ISSUER",
    "https://auth.platform.internal/realms/platform",
)
INSECURE_TLS_ENV = "NETENGINE_INSECURE_TLS"
CA_BUNDLE_ENV = "NETENGINE_CA_BUNDLE"
ADMIN_ROLES = {"admin", "netengine-admin", "operator-admin"}
POST_PHASE4_BOOTSTRAP_ENV = "NETENGINES_BOOTSTRAP_SECRET_AFTER_PHASE4"

_bearer = HTTPBearer(auto_error=False)


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


def _is_mutating_request(request: Request) -> bool:
    return request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


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
        logger.warning(
            "%s is enabled; Keycloak TLS certificate verification is disabled. "
            "Use only in isolated development environments.",
            INSECURE_TLS_ENV,
        )
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

    secret = request.headers.get("X-Bootstrap-Secret", "")
    if not phase4_done:
        # Bootstrap phase: accept secret in X-Bootstrap-Secret header.
        if bootstrap_secret and secret == bootstrap_secret:
            return {"sub": "bootstrap", "roles": ["admin"]}
        # Also allow an unauthenticated health check
        if request.url.path.endswith("/health"):
            return {"sub": "anon"}
        raise HTTPException(
            status_code=401, detail="Bootstrap secret required (Phase 4 not yet complete)"
        )

    # Post-Phase 4, OIDC is the default authority. The bootstrap secret can still
    # be enabled as an explicit local break-glass credential for automation, but
    # it must be separately opted in so deployments do not accidentally retain a
    # static admin credential after identity bootstrap.
    if (
        _is_truthy(os.environ.get(POST_PHASE4_BOOTSTRAP_ENV))
        and bootstrap_secret
        and secret == bootstrap_secret
    ):
        return {"sub": "bootstrap", "roles": ["admin"]}

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
            with _keycloak_ssl_context(state) as ssl_context:
                async with session.post(
                    f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token/introspect",
                    data={"token": token, "client_id": client_id},
                    auth=aiohttp.BasicAuth(client_id, client_secret),
                    ssl=ssl_context,
                ) as resp:
                    if resp.status != 200:
                        raise HTTPException(
                            status_code=401,
                            detail=(
                                "Bearer token validation failed: Keycloak introspection "
                                f"returned HTTP {resp.status}"
                            ),
                        )
                    data = await resp.json()
                    if not data.get("active"):
                        raise HTTPException(
                            status_code=401,
                            detail="Bearer token validation failed: token is expired or inactive",
                        )
                    if _is_mutating_request(request) and not (ADMIN_ROLES & _extract_roles(data)):
                        raise HTTPException(status_code=403, detail="Admin role required")
                    return data
    except HTTPException:
        raise
    except (aiohttp.ClientConnectorCertificateError, ssl.SSLError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Bearer token validation failed: TLS verification failed while contacting "
                f"the OIDC issuer. Configure {CA_BUNDLE_ENV} or the runtime CA bundle; "
                f"set {INSECURE_TLS_ENV}=true only for explicit development opt-in. {exc}"
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Bearer token validation failed: auth service unavailable: {exc}",
        )


def _extract_roles(user: dict) -> set[str]:
    """Return normalized role names from Keycloak introspection or bootstrap users."""
    roles: set[str] = set()
    raw_roles = user.get("roles", [])
    if isinstance(raw_roles, list):
        roles.update(str(role) for role in raw_roles)

    realm_access = user.get("realm_access", {})
    if isinstance(realm_access, dict) and isinstance(realm_access.get("roles"), list):
        roles.update(str(role) for role in realm_access["roles"])

    resource_access = user.get("resource_access", {})
    if isinstance(resource_access, dict):
        for access in resource_access.values():
            if isinstance(access, dict) and isinstance(access.get("roles"), list):
                roles.update(str(role) for role in access["roles"])

    return roles


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """Require an authenticated operator with an administrative role."""
    roles = _extract_roles(user)
    if not (ADMIN_ROLES & roles):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
