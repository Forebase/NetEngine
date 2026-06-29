"""All operator API route handlers for NetEngine M8.

Auth dependency is imported from .auth; the FastAPI app instance lives in .app.
All routes are registered via the router prefix /api/v1.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from netengine.api.auth import require_admin, require_auth
from netengine.core.reload import ReloadResult, apply_reload, check_immutability, compute_diff
from netengine.core.state import RuntimeState
from netengine.events.queues import PRIMARY_QUEUES, Queue, dlq_for
from netengine.logging import get_logger
from netengine.phase_labels import PHASE_LABELS
from netengine.spec.loader import SpecLoadError, load_spec
from netengine.spec.models import NetEngineSpec

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1")


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

IMPORT_SCHEMA_VERSION = "netengine.import.v1"

PHASE_REQUIRED_OUTPUTS = {
    "0": ("substrate_output",),
    "1": ("dns_output",),
    "2": ("dns_output",),
    "3": ("pki_bootstrapped",),
    "4": ("identity_platform_output",),
    "5": ("world_registry_output", "domain_registry_output"),
    "6": ("identity_inworld_output",),
    "7": ("ands_output",),
    "8": ("world_services_output",),
    "9": ("org_apps_output",),
}


def _validate_import_phase_state(state: RuntimeState) -> None:
    """Reject import snapshots with invalid phase completion state."""
    unknown = sorted(set(state.phase_completed) - set(PHASE_REQUIRED_OUTPUTS))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown phase ID(s): {', '.join(unknown)}"
        )

    completed = {
        phase for phase, is_completed in state.phase_completed.items() if is_completed
    }
    for phase in completed:
        required_outputs = PHASE_REQUIRED_OUTPUTS[phase]
        missing = [
            field for field in required_outputs if not getattr(state, field, None)
        ]
        if missing:
            missing_str = ", ".join(missing)
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Phase {phase} is completed but missing required "
                    f"output(s): {missing_str}"
                ),
            )

    completed_ints = sorted(int(phase) for phase in completed)
    if completed_ints:
        expected = set(range(completed_ints[-1] + 1))
        missing_prereqs = sorted(expected - set(completed_ints))
        if missing_prereqs:
            missing_str = ", ".join(str(phase) for phase in missing_prereqs)
            raise HTTPException(
                status_code=422,
                detail=(
                    "Impossible phase combination; missing prerequisite "
                    f"phase(s): {missing_str}"
                ),
            )


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
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get(
            "lifecycle", "ephemeral"
        )
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


class ServiceToggleRequest(BaseModel):
    enabled: bool


@router.put("/services/{name}")
async def update_service(
    name: str, body: ServiceToggleRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Enable or disable a named world service (mail, storage) in the spec."""
    state = RuntimeState.load()

    if not state.world_spec:
        raise HTTPException(status_code=409, detail="No world spec loaded")

    world_services = state.world_spec.get("world_services") or {}
    if name not in world_services:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{name}' not found — known services: {list(world_services)}",
        )

    world_services[name]["enabled"] = body.enabled
    state.world_spec["world_services"] = world_services
    state.save()

    return {"status": "updated", "service": name, "enabled": body.enabled}


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


@router.get("/orgs")
async def list_orgs(user: dict = Depends(require_auth)) -> Any:
    """List all organisations in the world registry."""
    from netengine.handlers.world_registry_handler import WorldRegistryHandler

    handler = WorldRegistryHandler()
    return await handler.list_orgs()


@router.get("/orgs/{org}")
async def get_org(org: str, user: dict = Depends(require_auth)) -> Any:
    """Return a single organisation by name."""
    from netengine.handlers.world_registry_handler import WorldRegistryHandler

    handler = WorldRegistryHandler()
    result = await handler.get_org(org)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Org {org} not found")
    return result


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


class OrgUpdateRequest(BaseModel):
    capabilities: list[str] = []
    and_profile: str = "business"


@router.put("/orgs/{org}")
async def update_org(
    org: str, body: OrgUpdateRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Update an organisation's capabilities and AND profile."""
    from netengine.handlers.world_registry_handler import WorldRegistryHandler

    handler = WorldRegistryHandler()
    existing = await handler.get_org(org)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Org {org} not found")
    await handler.update_org(org, body.capabilities, body.and_profile)
    return {"status": "updated", "org": org}


class OrgRemoveRequest(BaseModel):
    confirm: bool = False


@router.delete("/orgs/{org}")
async def remove_org(
    org: str, body: OrgRemoveRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Remove an organisation from the world registry.

    Persistent worlds require confirm=true in the request body.
    """
    state = RuntimeState.load()
    if state.world_spec:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get("lifecycle", "ephemeral")
        if raw_lifecycle == "persistent" and not body.confirm:
            raise HTTPException(
                status_code=409,
                detail="Org removal from a persistent world requires confirm=true",
            )

    from netengine.handlers.world_registry_handler import WorldRegistryHandler

    handler = WorldRegistryHandler()
    removed = await handler.remove_org(org)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Org {org} not found")
    return {"status": "removed", "org": org}


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
# ANDs
# ─────────────────────────────────────────────


class ANDCreateRequest(BaseModel):
    name: str
    org: str
    profile: str = "business"
    dns_suffix: str = ""


@router.get("/ands")
async def list_ands(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """List provisioned AND instances from runtime state."""
    state = RuntimeState.load()
    ands_out = state.ands_output or {}
    return {"ands": ands_out.get("instances", [])}


@router.post("/ands")
async def create_and(body: ANDCreateRequest, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Provision a new AND for an org."""
    from netengine.core.supabase_client import get_db

    db = await get_db()
    org_result = (
        await db.table("world_registry").select("org_name").eq("org_name", body.org).execute()
    )
    if not org_result.data:
        raise HTTPException(status_code=404, detail=f"Org {body.org} not found in world registry")

    dns_suffix = body.dns_suffix or f"{body.org}.internal"
    record = {
        "and_name": body.name,
        "org_name": body.org,
        "profile": body.profile,
        "dns_suffix": dns_suffix,
    }
    await db.table("and_instances").upsert(record).execute()

    state = RuntimeState.load()
    ands_out = state.ands_output or {}
    instances: list[dict[str, Any]] = ands_out.get("instances", [])
    if not any(i.get("and_name") == body.name for i in instances):
        instances.append(record)
        ands_out["instances"] = instances
        state.ands_output = ands_out
        state.save()

    return {"status": "provisioned", "and": body.name, "org": body.org, "dns_suffix": dns_suffix}


class ANDProfileUpdateRequest(BaseModel):
    profile: str
    dns_suffix: str = ""


@router.put("/ands/{and_name}/profile")
async def update_and_profile(
    and_name: str, body: ANDProfileUpdateRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Update the profile (and optionally dns_suffix) of a provisioned AND instance."""
    state = RuntimeState.load()

    if not state.world_spec:
        raise HTTPException(status_code=409, detail="No world spec loaded")

    ands_out = state.ands_output or {}
    instances: list[dict[str, Any]] = ands_out.get("instances", [])
    instance = next((i for i in instances if i.get("and_name") == and_name), None)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"AND instance '{and_name}' not found")
    profiles = (state.world_spec.get("ands") or {}).get("profiles", {})
    if body.profile not in profiles:
        raise HTTPException(
            status_code=422,
            detail=f"Profile '{body.profile}' not defined in spec"
            f" — known profiles: {list(profiles)}",
        )

    instance["profile"] = body.profile
    if body.dns_suffix:
        instance["dns_suffix"] = body.dns_suffix

    state.ands_output = ands_out
    state.save()

    return {"status": "updated", "and": and_name, "profile": body.profile}


@router.delete("/ands/{and_name}")
async def remove_and(and_name: str, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Remove an AND instance."""
    from netengine.core.supabase_client import get_db

    db = await get_db()
    existing = await db.table("and_instances").select("and_name").eq("and_name", and_name).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail=f"AND {and_name} not found")
    await db.table("and_instances").delete().eq("and_name", and_name).execute()

    state = RuntimeState.load()
    ands_out = state.ands_output or {}
    instances = [i for i in ands_out.get("instances", []) if i.get("and_name") != and_name]
    ands_out["instances"] = instances
    state.ands_output = ands_out
    state.save()

    return {"status": "removed", "and": and_name}


# ─────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────


@router.get("/registry/domains")
async def list_domains(user: dict = Depends(require_auth)) -> Any:
    from netengine.core.supabase_client import get_db

    db = await get_db()
    result = await db.table("domain_records").select("*").execute()
    return result.data


class DomainRegisterRequest(BaseModel):
    domain: str
    org: str
    record_type: str = "A"
    value: str = ""


@router.post("/registry/domains")
async def register_domain(
    body: DomainRegisterRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Register a domain in the domain registry."""
    from netengine.core.supabase_client import get_db

    db = await get_db()
    record = {
        "domain": body.domain,
        "org_name": body.org,
        "record_type": body.record_type,
        "value": body.value,
    }
    await db.table("domain_records").upsert(record).execute()
    return {"status": "registered", "domain": body.domain, "org": body.org}


@router.delete("/registry/domains/{domain:path}")
async def remove_domain(domain: str, user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Remove a domain from the domain registry."""
    from netengine.core.supabase_client import get_db

    db = await get_db()
    await db.table("domain_records").delete().eq("domain", domain).execute()
    return {"status": "removed", "domain": domain}


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
# Gateway
# ─────────────────────────────────────────────


class GatewayUpdateRequest(BaseModel):
    real_internet_mode: str = ""
    upstream_resolver_enabled: bool | None = None
    upstream_resolver_ip: str = ""
    cross_world_mode: str = ""


@router.put("/gateway")
async def update_gateway(
    body: GatewayUpdateRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Update gateway portal configuration in the running spec.

    Fields left at their zero-values are not modified. Changes take effect on
    the next bootstrap cycle; live gateway reconfiguration requires a full reload.
    """
    state = RuntimeState.load()

    if not state.world_spec:
        raise HTTPException(status_code=409, detail="No world spec loaded")

    gw = state.world_spec.get("gateway_portal") or {}
    real_internet = gw.get("real_internet") or {}
    cross_world = gw.get("cross_world") or {}

    valid_ri_modes = {"isolated", "shadowed", "mirrored", "exposed", "custom"}
    valid_cw_modes = {"none", "peered", "federated"}

    if body.real_internet_mode:
        if body.real_internet_mode not in valid_ri_modes:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid real_internet_mode '{body.real_internet_mode}'"
                f" — valid: {valid_ri_modes}",
            )
        real_internet["mode"] = body.real_internet_mode

    if body.upstream_resolver_enabled is not None:
        real_internet["upstream_resolver_enabled"] = body.upstream_resolver_enabled

    if body.upstream_resolver_ip:
        real_internet["upstream_resolver_ip"] = body.upstream_resolver_ip

    if body.cross_world_mode:
        if body.cross_world_mode not in valid_cw_modes:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid cross_world_mode '{body.cross_world_mode}'"
                f" — valid: {valid_cw_modes}",
            )
        cross_world["mode"] = body.cross_world_mode

    gw["real_internet"] = real_internet
    gw["cross_world"] = cross_world
    state.world_spec["gateway_portal"] = gw
    state.save()

    return {"status": "updated", "gateway_portal": gw}


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


@router.get("/pki/intermediate-ca-cert")
async def get_intermediate_ca_cert(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Return the intermediate CA certificate PEM, if intermediate CA is enabled.

    Clients that need to build a full trust chain should fetch this cert and
    add it alongside the root CA cert (available in GET /world as ca_cert_present).
    """
    state = RuntimeState.load()
    if not state.intermediate_ca_cert:
        raise HTTPException(
            status_code=404,
            detail="Intermediate CA certificate not available; ensure pki.intermediate_ca_enabled is true and PKI phase has completed",
        )
    return {
        "intermediate_ca_cert": state.intermediate_ca_cert,
        "available": True,
    }


class PKIRotationPolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    default_interval_hours: int | None = None
    default_warning_days: int | None = None
    cert_type_overrides: dict[str, Any] | None = None


@router.put("/pki/rotation-policy")
async def update_pki_rotation_policy(
    body: PKIRotationPolicyUpdateRequest, user: dict = Depends(require_auth)
) -> dict[str, Any]:
    """Update PKI certificate rotation policy in the running spec.

    Only fields provided (non-None) are updated; omitted fields are left as-is.
    Changes are picked up by the rotation worker on its next iteration without restart.
    """
    state = RuntimeState.load()

    if not state.world_spec:
        raise HTTPException(status_code=409, detail="No world spec loaded")

    pki = state.world_spec.get("pki") or {}
    policy = pki.get("rotation_policy") or {}

    if body.enabled is not None:
        policy["enabled"] = body.enabled
    if body.default_interval_hours is not None:
        if body.default_interval_hours < 1:
            raise HTTPException(status_code=422, detail="default_interval_hours must be >= 1")
        policy["default_interval_hours"] = body.default_interval_hours
    if body.default_warning_days is not None:
        if body.default_warning_days < 1:
            raise HTTPException(status_code=422, detail="default_warning_days must be >= 1")
        policy["default_warning_days"] = body.default_warning_days
    if body.cert_type_overrides is not None:
        policy["cert_type_overrides"] = body.cert_type_overrides

    pki["rotation_policy"] = policy
    state.world_spec["pki"] = pki
    state.save()

    return {"status": "updated", "rotation_policy": policy}


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


@router.get("/queues")
async def get_queue_state(user: dict = Depends(require_auth)) -> dict[str, Any]:
    """Return pgmq queue depths and DLQ state for all handler boundaries."""
    from netengine.core.supabase_client import get_db

    try:
        db = await get_db()
        queue_stats: list[dict[str, Any]] = []
        for q in PRIMARY_QUEUES:
            try:
                result = await db.rpc("pgmq_metrics", {"queue_name": q}).execute()
                metrics = result.data[0] if result.data else {}
            except Exception:
                metrics = {}

            try:
                dlq_result = await db.rpc("pgmq_metrics", {"queue_name": dlq_for(q)}).execute()
                dlq_metrics = dlq_result.data[0] if dlq_result.data else {}
            except Exception:
                dlq_metrics = {}

            queue_stats.append(
                {
                    "queue": q,
                    "depth": metrics.get("queue_length", 0),
                    "oldest_msg_age_sec": metrics.get("oldest_msg_age_sec"),
                    "dlq": dlq_for(q),
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

    primary_queue_names = {q.value for q in PRIMARY_QUEUES}
    if queue_name not in primary_queue_names:
        raise HTTPException(status_code=404, detail=f"Unknown queue: {queue_name}")

    queue = Queue(queue_name)
    dlq = dlq_for(queue)

    client = PGMQClient()
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
            await client.send(queue, envelope)
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


def _sanitize_export_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_export_value(child)
            for key, child in value.items()
            if not _is_secret_field(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_export_value(child) for child in value]
    if isinstance(value, str) and _contains_private_pem(value):
        return None
    return value


# ─────────────────────────────────────────────
# Export / Import
# ─────────────────────────────────────────────


@router.get("/export")
async def export_world(user: dict = Depends(require_admin)) -> dict[str, Any]:
    """Return exportable world state snapshot (spec + runtime state)."""
    state = RuntimeState.load()
    import datetime as _dt

    return {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "exported_at": _dt.datetime.utcnow().isoformat(),
        "spec": state.world_spec,
        "phase_completed": state.phase_completed,
        "ca_cert_pem": state.ca_cert_pem,
        # ca_cert_pem is the public CA certificate used by clients to validate trust;
        # private CA material is not stored in this field. Sanitize mutable phase
        # outputs defensively so private keys/secrets are never exported if a
        # handler accidentally records them in runtime state.
        "pki_output": _sanitize_export_value(state.pki_output),
        "dns_output": _sanitize_export_value(state.dns_output),
        "ands_output": _sanitize_export_value(state.ands_output),
        "world_services_output": _sanitize_export_value(state.world_services_output),
    }


class ImportRequest(BaseModel):
    schema_version: str = Field(
        ..., description="Import snapshot schema/version identifier"
    )
    spec: dict[str, Any]
    phase_completed: dict[str, bool] = Field(default_factory=dict)
    ca_cert_pem: str | None = None
    substrate_output: dict[str, Any] | None = None
    pki_output: dict[str, Any] | None = None
    dns_output: dict[str, Any] | None = None
    identity_platform_output: dict[str, Any] | None = None
    world_registry_output: dict[str, Any] | None = None
    domain_registry_output: dict[str, Any] | None = None
    identity_inworld_output: dict[str, Any] | None = None
    ands_output: dict[str, Any] | None = None
    world_services_output: dict[str, Any] | None = None
    org_apps_output: dict[str, Any] | None = None


@router.post("/import")
async def import_world(body: ImportRequest, user: dict = Depends(require_admin)) -> dict[str, Any]:
    """Restore world state from an export snapshot (persistent mode only)."""
    state = RuntimeState.load()
    if state.world_spec:
        raw_lifecycle = (state.world_spec.get("metadata") or {}).get(
            "lifecycle", "ephemeral"
        )
        if raw_lifecycle == "ephemeral":
            raise HTTPException(
                status_code=409, detail="Import is only valid for persistent worlds"
            )

    if body.schema_version != IMPORT_SCHEMA_VERSION:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported import schema_version: {body.schema_version}",
        )

    try:
        spec = NetEngineSpec.model_validate(body.spec)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Spec parse error: {exc}")

    imported_state = RuntimeState(
        world_spec=spec.model_dump(mode="json"),
        phase_completed=dict(body.phase_completed),
        ca_cert_pem=body.ca_cert_pem,
        substrate_output=body.substrate_output,
        pki_output=body.pki_output,
        dns_output=body.dns_output,
        identity_platform_output=body.identity_platform_output,
        world_registry_output=body.world_registry_output,
        domain_registry_output=body.domain_registry_output,
        identity_inworld_output=body.identity_inworld_output,
        ands_output=body.ands_output,
        world_services_output=body.world_services_output,
        org_apps_output=body.org_apps_output,
        pki_bootstrapped=bool(body.ca_cert_pem or body.pki_output),
    )
    _validate_import_phase_state(imported_state)

    phases_restored = [
        phase for phase, completed in body.phase_completed.items() if completed
    ]
    imported_state.save()
    phases_restored = list(body.phase_completed.keys())
    state.world_spec = body.spec
    state.phase_completed = dict(body.phase_completed)
    if body.ca_cert_pem:
        state.ca_cert_pem = body.ca_cert_pem
    if body.pki_output:
        state.pki_output = _sanitize_export_value(body.pki_output)
    if body.dns_output:
        state.dns_output = _sanitize_export_value(body.dns_output)
    if body.ands_output:
        state.ands_output = _sanitize_export_value(body.ands_output)
    if body.world_services_output:
        state.world_services_output = _sanitize_export_value(body.world_services_output)
    state.save()
    return {"status": "imported", "phases_restored": phases_restored}


# ─────────────────────────────────────────────
# Prometheus metrics scrape endpoint
# ─────────────────────────────────────────────


@router.get("/metrics")
async def prometheus_metrics() -> Any:
    """Expose Prometheus metrics for scraping."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    from starlette.responses import Response

    from netengine.monitoring.metrics import REGISTRY

    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
