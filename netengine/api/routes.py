"""All operator API route handlers for NetEngine M8.

Auth dependency is imported from .auth; the FastAPI app instance lives in .app.
All routes are registered via the router prefix /api/v1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from netengine.api.auth import require_auth
from netengine.core.reload import ReloadResult, apply_reload, check_immutability, compute_diff
from netengine.core.state import RuntimeState
from netengine.spec.loader import SpecLoadError, load_spec
from netengine.spec.models import NetEngineSpec

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

PHASE_LABELS = {
    "0": "Substrate",
    "1": "DNS root + platform zones",
    "2": "DNS TLD hierarchy",
    "3": "PKI + ACME",
    "4": "Platform identity",
    "5": "Registries",
    "6": "In-world identity",
    "7": "ANDs",
    "8": "Services",
}


@router.get("/health")
async def health() -> dict[str, Any]:
    """Per-phase healthcheck status."""
    state = RuntimeState.load()
    phases = {}
    for phase_id, label in PHASE_LABELS.items():
        completed = state.phase_completed.get(phase_id, False)
        phases[phase_id] = {"label": label, "completed": completed}
    overall = "ok" if all(p["completed"] for p in phases.values()) else "degraded"
    return {
        "status": overall,
        "phases": phases,
        "last_error": state.last_error,
    }


# ─────────────────────────────────────────────
# World
# ─────────────────────────────────────────────


@router.get("/world")
async def get_world(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Return current spec and runtime state."""
    state = RuntimeState.load()
    return {
        "spec": state.world_spec,
        "phase_completed": state.phase_completed,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        "last_error": state.last_error,
        "ca_cert_present": bool(state.ca_cert_pem),
    }


class ReloadRequest(BaseModel):
    spec_yaml: str


@router.post("/reload")
async def reload_world(body: ReloadRequest, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Diff a new spec against the running world and apply changes in dep order.

    Rejects entirely if any immutable field changed.
    Ephemeral: apply immediately.
    Persistent: refuses PKI reconfig and org removal.
    """
    state = RuntimeState.load()

    if not state.world_spec:
        raise HTTPException(
            status_code=409, detail="No world is currently running — use netengines up first"
        )

    # Parse incoming spec
    try:
        raw = yaml.safe_load(body.spec_yaml)
        new_spec = NetEngineSpec(**raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Spec parse error: {exc}")

    # Reconstruct old spec from persisted state
    try:
        old_spec = NetEngineSpec(**state.world_spec)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Stored spec is corrupt: {exc}")

    is_ephemeral = old_spec.metadata.lifecycle.value == "ephemeral"

    result: ReloadResult = await apply_reload(old_spec, new_spec, state, is_ephemeral=is_ephemeral)

    status_code = 200 if result.success else 422
    response = {
        "success": result.success,
        "applied": [
            {"section": e.section, "change_type": e.change_type, "detail": e.detail}
            for e in result.applied
        ],
        "rejected": [
            {"section": e.section, "change_type": e.change_type, "detail": e.detail}
            for e in result.rejected
        ],
        "errors": result.errors,
        "immutability_violations": result.immutability_violations,
    }
    if not result.success:
        raise HTTPException(status_code=status_code, detail=response)
    return response


class WorldTeardownRequest(BaseModel):
    confirm: bool = False


@router.delete("/world")
async def teardown_world(
    body: WorldTeardownRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Tear down the running world.

    Ephemeral: proceeds immediately.
    Persistent: requires confirm=true in the request body.
    """
    state = RuntimeState.load()
    if state.world_spec:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get("lifecycle", "ephemeral")
        if raw_lifecycle == "persistent" and not body.confirm:
            raise HTTPException(
                status_code=409,
                detail="Persistent world teardown requires confirm=true in request body",
            )

    removed: list[str] = []
    errors: list[str] = []

    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
        for container in client.containers.list():
            if container.name.startswith("netengines_"):
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                    removed.append(container.name)
                except Exception as exc:
                    errors.append(f"container {container.name}: {exc}")

        for network in client.networks.list():
            if network.name.startswith("netengines_"):
                try:
                    network.remove()
                    removed.append(f"network:{network.name}")
                except Exception as exc:
                    errors.append(f"network {network.name}: {exc}")

        for volume in client.volumes.list():
            if volume.name.startswith("netengines_"):
                try:
                    volume.remove(force=True)
                    removed.append(f"volume:{volume.name}")
                except Exception as exc:
                    errors.append(f"volume {volume.name}: {exc}")

    except Exception as exc:
        errors.append(f"Docker teardown error: {exc}")

    # Clear runtime state
    state_file = __import__("netengine.core.state", fromlist=["get_state_file"]).get_state_file()
    try:
        if state_file.exists():
            state_file.unlink()
    except Exception as exc:
        errors.append(f"State file removal: {exc}")

    return {"removed": removed, "errors": errors}


# ─────────────────────────────────────────────
# Services
# ─────────────────────────────────────────────


@router.get("/services")
async def get_services(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """List running NetEngines containers and their status."""
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
        containers = [
            {"name": c.name, "status": c.status, "image": c.image.tags}
            for c in client.containers.list(all=True)
            if c.name.startswith("netengines_")
        ]
    except Exception as exc:
        containers = []
        logger.warning(f"Docker unavailable: {exc}")
    state = RuntimeState.load()
    return {"containers": containers, "phase_completed": state.phase_completed}


# ─────────────────────────────────────────────
# Orgs
# ─────────────────────────────────────────────


class OrgAdmitRequest(BaseModel):
    name: str
    description: str = ""
    capabilities: list[str] = []
    and_profile: str = "business"


@router.post("/orgs")
async def admit_org(body: OrgAdmitRequest, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Admit a new organisation to the world registry and trigger provisioning."""
    from netengine.handlers.world_registry_handler import WorldRegistryHandler

    handler = WorldRegistryHandler()
    await handler.admit_org(
        name=body.name,
        capabilities=body.capabilities,
        and_profile=body.and_profile,
    )
    return {"status": "admitted", "org": body.name}


class AppDeployRequest(BaseModel):
    app: str
    subdomain: str = ""
    config: dict[str, Any] = {}


@router.post("/orgs/{org}/apps")
async def deploy_app(
    org: str, body: AppDeployRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Deploy a catalog app into an org's AND (container → DNS → cert → OIDC)."""
    from netengine.core.supabase_client import get_db

    db = await get_db()
    result = await db.table("world_registry").select("org_name").eq("org_name", org).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Org {org} not found in world registry")

    from netengine.handlers.app_handler import AppHandler
    from netengine.handlers.dns import DNSHandler
    from netengine.handlers.docker_handler import DockerHandler
    from netengine.handlers.oidc_handler import OIDCHandler
    from netengine.handlers.pki_handler import PKIHandler

    state = RuntimeState.load()
    docker = DockerHandler()
    dns = DNSHandler()
    pki = PKIHandler(docker, state, {})
    oidc = OIDCHandler(
        keycloak_url="https://auth.internal",
        admin_username="admin",
        admin_password=state.inworld_admin_password or "",
    )
    handler = AppHandler(docker, dns, pki, oidc, state)
    subdomain = body.subdomain or body.app
    deployment = await handler.deploy_app(org, body.app, subdomain, body.config)
    return deployment


# ─────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────


@router.get("/registry/domains")
async def list_domains(user: dict = Depends(require_auth)) -> Any:
    from netengine.core.supabase_client import get_db

    db = await get_db()
    result = await db.table("domain_records").select("*").execute()
    return result.data


@router.get("/registry/addresses")
async def list_addresses(user: dict = Depends(require_auth)) -> Any:
    from netengine.core.supabase_client import get_db

    db = await get_db()
    result = await db.table("address_leases").select("*").execute()
    return result.data


# ─────────────────────────────────────────────
# DNS proxy
# ─────────────────────────────────────────────


@router.get("/dns/{domain:path}")
async def dns_query(
    domain: str, record_type: str = "A", user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Proxy a DNS query into the in-world resolver."""
    state = RuntimeState.load()
    dns_output = state.dns_output or {}
    root_ip = dns_output.get("root_ip", "10.0.0.2")

    try:
        proc = await asyncio.create_subprocess_exec(
            "dig",
            f"@{root_ip}",
            domain,
            record_type,
            "+short",
            "+time=3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        answers = [line for line in stdout.decode().strip().splitlines() if line]
        return {"domain": domain, "type": record_type, "answers": answers, "resolver": root_ip}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DNS query failed: {exc}")


# ─────────────────────────────────────────────
# PKI
# ─────────────────────────────────────────────


@router.get("/pki/certs")
async def list_certs(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """List issued certs tracked in runtime state and step-ca inventory."""
    state = RuntimeState.load()
    pki_out = state.pki_output or {}

    # Pull cert list from step-ca admin API if the CA is running
    step_ca_ip = pki_out.get("step_ca_ip") or "10.0.0.6"
    ca_cert_pem = state.ca_cert_pem

    certs: list[dict[str, Any]] = []
    if ca_cert_pem:
        # step-ca admin API: GET /admin/provisioners (MVP: return known certs from state)
        known = pki_out.get("issued_certs", [])
        certs = known if isinstance(known, list) else []

    return {
        "ca_cert_present": bool(ca_cert_pem),
        "step_ca_ip": step_ca_ip,
        "issued_certs": certs,
    }


# ─────────────────────────────────────────────
# Identity
# ─────────────────────────────────────────────


@router.get("/identity/realms")
async def list_realms(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Return provisioned Keycloak realms and user counts from runtime state."""
    state = RuntimeState.load()
    platform_out = state.identity_platform_output or {}
    inworld_out = state.identity_inworld_output or {}
    return {
        "platform_realm": {
            "realm": platform_out.get("realm_name", "platform"),
            "issuer": platform_out.get("issuer"),
            "users": platform_out.get("user_count", 0),
        },
        "inworld_realm": {
            "realm": inworld_out.get("realm_name", "inworld"),
            "issuer": inworld_out.get("issuer"),
            "orgs": inworld_out.get("org_realms", []),
        },
    }


# ─────────────────────────────────────────────
# Event queue / DLQ
# ─────────────────────────────────────────────

KNOWN_QUEUES = [
    "dns_updates",
    "oidc_provisioning",
    "and_provisioning",
    "mail_provisioning",
    "app_deployments",
]


@router.get("/queues")
async def get_queue_state(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Return pgmq queue depths and DLQ state for all handler boundaries."""
    from netengine.core.supabase_client import get_db

    try:
        db = await get_db()
        queue_stats: list[dict[str, Any]] = []
        for q in KNOWN_QUEUES:
            try:
                result = await db.rpc("pgmq_metrics", {"queue_name": q}).execute()
                metrics = result.data[0] if result.data else {}
            except Exception:
                metrics = {}

            try:
                dlq_result = await db.rpc("pgmq_metrics", {"queue_name": f"{q}_dlq"}).execute()
                dlq_metrics = dlq_result.data[0] if dlq_result.data else {}
            except Exception:
                dlq_metrics = {}

            queue_stats.append(
                {
                    "queue": q,
                    "depth": metrics.get("queue_length", 0),
                    "oldest_msg_age_sec": metrics.get("oldest_msg_age_sec"),
                    "dlq": f"{q}_dlq",
                    "dlq_depth": dlq_metrics.get("queue_length", 0),
                }
            )
        return {"queues": queue_stats}
    except Exception as exc:
        # Supabase not available (e.g. ephemeral world, no DB connection)
        return {"queues": [], "error": str(exc)}


@router.post("/queues/{queue_name}/dlq/replay")
async def replay_dlq(queue_name: str, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Move all messages from a DLQ back to the main queue for retry."""
    from netengine.core.pgmq_client import PGMQClient

    client = PGMQClient()
    dlq = f"{queue_name}_dlq"
    replayed = 0
    errors: list[str] = []
    while True:
        try:
            msg = await client.receive(dlq, timeout=1)
            if not msg:
                break
            # Re-send to main queue and delete from DLQ
            import json as _json

            from netengine.events.schema import EventEnvelope

            envelope = EventEnvelope(**_json.loads(msg["message"]))
            envelope.retry_count = 0  # reset retry counter
            await client.send(queue_name, envelope)
            await client.delete(dlq, msg["msg_id"])
            replayed += 1
        except Exception as exc:
            errors.append(str(exc))
            break
    return {"replayed": replayed, "errors": errors}


@router.get("/events/{correlation_id}")
async def get_event_chain(
    correlation_id: str, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Return full causal event chain for a correlation ID from pgmq archive."""
    from netengine.core.supabase_client import get_db

    try:
        db = await get_db()
        result = (
            await db.table("pgmq_archive")
            .select("*")
            .eq("correlation_id", correlation_id)
            .execute()
        )
        events = result.data or []
        return {"correlation_id": correlation_id, "events": events, "count": len(events)}
    except Exception as exc:
        return {"correlation_id": correlation_id, "events": [], "error": str(exc)}


# ─────────────────────────────────────────────
# Export / Import
# ─────────────────────────────────────────────


@router.get("/export")
async def export_world(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Return exportable world state snapshot (spec + runtime state)."""
    state = RuntimeState.load()
    import datetime as _dt

    return {
        "exported_at": _dt.datetime.utcnow().isoformat(),
        "spec": state.world_spec,
        "phase_completed": state.phase_completed,
        "ca_cert_pem": state.ca_cert_pem,
        "pki_output": state.pki_output,
        "dns_output": state.dns_output,
        "ands_output": state.ands_output,
        "world_services_output": state.world_services_output,
    }


class ImportRequest(BaseModel):
    spec: dict[str, Any]
    phase_completed: dict[str, bool] = {}
    ca_cert_pem: str | None = None
    pki_output: dict[str, Any] | None = None
    dns_output: dict[str, Any] | None = None
    ands_output: dict[str, Any] | None = None
    world_services_output: dict[str, Any] | None = None


@router.post("/import")
async def import_world(body: ImportRequest, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Restore world state from an export snapshot (persistent mode only)."""
    state = RuntimeState.load()
    if state.world_spec:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get("lifecycle", "ephemeral")
        if raw_lifecycle == "ephemeral":
            raise HTTPException(
                status_code=409, detail="Import is only valid for persistent worlds"
            )

    phases_restored = list(body.phase_completed.keys())
    state.world_spec = body.spec
    state.phase_completed = dict(body.phase_completed)
    if body.ca_cert_pem:
        state.ca_cert_pem = body.ca_cert_pem
    if body.pki_output:
        state.pki_output = body.pki_output
    if body.dns_output:
        state.dns_output = body.dns_output
    if body.ands_output:
        state.ands_output = body.ands_output
    if body.world_services_output:
        state.world_services_output = body.world_services_output
    state.save()
    return {"status": "imported", "phases_restored": phases_restored}
