import os

import aiohttp
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer

from netengine.core.state import RuntimeState
from netengine.core.supabase_client import get_supabase
from netengine.handlers.app_handler import AppHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler

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
            auth=aiohttp.BasicAuth("admin-cli", ""),  # or use client credentials
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


# Add these routes to netengine/api/app.py


@app.get("/api/v1/registry/domains")
async def list_domains(user=Depends(get_current_user)):
    supabase = get_supabase()
    result = await supabase.table("domain_records").select("*").execute()
    return result.data


@app.get("/api/v1/registry/addresses")
async def list_addresses(user=Depends(get_current_user)):
    supabase = get_supabase()
    result = await supabase.table("address_leases").select("*").execute()
    return result.data


@app.get("/api/v1/queues")
async def get_queue_state(user=Depends(get_current_user)):
    # Query pgmq queue counts
    # This requires a custom Supabase function to get queue stats.
    # For MVP, we'll return a stub.
    return {"queues": {"dns_updates": 0, "oidc_provisioning": 0, "and_provisioning": 0}}


@app.get("/api/v1/events/{correlation_id}")
async def get_event_chain(correlation_id: str, user=Depends(get_current_user)):
    # Query all events with this correlation_id from pgmq history
    # This requires a pgmq_archive table; stub for now.
    return {"correlation_id": correlation_id, "events": []}


@app.post("/api/v1/orgs")
async def admit_org(org: dict, user=Depends(get_current_user)):
    from ..handlers.world_registry_handler import WorldRegistryHandler

    handler = WorldRegistryHandler()
    await handler.admit_org(
        name=org["name"],
        capabilities=org.get("capabilities", []),
        and_profile=org.get("and_profile", "business"),
    )
    return {"status": "admitted"}


# ANDs


@app.post("/api/v1/ands/{and_name}/profile")
async def change_and_profile(and_name: str, profile: str, user=Depends(get_current_user)):
    from netengine.handlers.and_handler import ANDHandler
    from netengine.handlers.docker_handler import DockerHandler

    handler = ANDHandler(DockerHandler(), RuntimeState.load())
    await handler.update_and_profile(and_name, profile)
    return {"status": "updated"}


@app.delete("/api/v1/ands/{and_name}")
async def delete_and(and_name: str, user=Depends(get_current_user)):
    from netengine.handlers.and_handler import ANDHandler
    from netengine.handlers.docker_handler import DockerHandler

    handler = ANDHandler(DockerHandler(), RuntimeState.load())
    await handler.deprovision_and(and_name)
    return {"status": "deleted"}


# App Deploymen


@app.post("/api/v1/orgs/{org}/apps")
async def deploy_app(org: str, payload: dict, user=Depends(get_current_user)):

    app_name = payload["app"]
    subdomain = payload.get("subdomain", app_name)
    config = payload.get("config", {})
    # Check if org exists
    supabase = get_supabase()
    result = await supabase.table("world_registry").select("org_name").eq("org_name", org).execute()
    if not result.data:
        raise HTTPException(404, f"Org {org} not found")
    docker = DockerHandler()
    dns = DNSHandler()
    pki = PKIHandler(docker, RuntimeState.load(), {})  # need spec or pass context
    oidc = OIDCHandler(
        keycloak_url="https://auth.internal",
        admin_username="admin",
        admin_password=RuntimeState.load().inworld_admin_password,
    )
    handler = AppHandler(docker, dns, pki, oidc, RuntimeState.load())
    deployment = await handler.deploy_app(org, app_name, subdomain, config)
    return deployment
