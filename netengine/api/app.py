# netengine/api/app.py
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
import os
import aiohttp
from netengine.core.state import RuntimeState
from netengine.core.supabase_client import get_supabase

app = FastAPI(title="NetEngine Operator API", version="0.1")

# Bootstrap secret (from env)
BOOTSTRAP_SECRET = os.environ.get("BOOTSTRAP_SECRET", "")
KEYCLOAK_ISSUER = "https://auth.platform.internal/realms/platform"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token")

# ─────────────────────────────────────────────
# Auth dependency – switches after Phase 4
# ─────────────────────────────────────────────
async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    # If Phase 4 not complete, use bootstrap secret
    state = RuntimeState.load()
    if not state.phase_completed.get("4", False):
        # Validate bootstrap secret (passed in X-Bootstrap-Secret header)
        secret = request.headers.get("X-Bootstrap-Secret")
        if secret != BOOTSTRAP_SECRET:
            raise HTTPException(status_code=401, detail="Invalid bootstrap secret")
        return {"sub": "bootstrap", "roles": ["admin"]}

    # Phase 4 complete – validate OIDC token via Keycloak introspection
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token/introspect",
            data={"token": token},
            auth=aiohttp.BasicAuth("admin-cli", "")  # or use client credentials
        ) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=401, detail="Invalid token")
            data = await resp.json()
            if not data.get("active"):
                raise HTTPException(status_code=401, detail="Token expired")
            return data

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.get("/api/v1/health")
async def health():
    return {"status": "ok"}

@app.get("/api/v1/world")
async def get_world(user=Depends(get_current_user)):
    state = RuntimeState.load()
    # Return spec and runtime state (filter sensitive data)
    return {"spec": state.world_spec, "state": state.__dict__}

@app.get("/api/v1/services")
async def get_services(user=Depends(get_current_user)):
    # Query running containers via Docker
    from netengine.handlers.docker_handler import DockerHandler
    docker = DockerHandler()
    containers = docker.client.containers.list()
    return {"containers": [{"name": c.name, "status": c.status} for c in containers]}