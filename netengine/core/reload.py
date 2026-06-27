"""Spec diff and live-reload engine.

Computes the delta between the currently-running spec and a new spec, then
applies changes in bootstrap dependency order.  Immutable fields are checked
first; any change to them rejects the entire reload before any handler runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from netengine.core.state import RuntimeState
from netengine.logging import get_logger
from netengine.spec.models import NetEngineSpec

logger = get_logger(__name__)


def _collect_immutable_paths(model_cls: type[BaseModel], prefix: str = "") -> dict[str, str]:
    """Walk a Pydantic model tree and collect dot-paths marked with immutable=True.

    Fields use json_schema_extra={"immutable": True, "immutable_reason": "..."} to
    declare immutability. Adding a new field automatically registers it here — no
    manual list to maintain.
    """
    paths: dict[str, str] = {}
    for name, field_info in model_cls.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        extra = field_info.json_schema_extra
        if isinstance(extra, dict) and extra.get("immutable"):
            paths[path] = str(extra.get("immutable_reason", "immutable field"))
        # Recurse into direct SpecModel subclass fields (non-optional nested models).
        ann = field_info.annotation
        if (
            ann is not None
            and isinstance(ann, type)
            and issubclass(ann, BaseModel)
            and ann is not model_cls
        ):
            paths.update(_collect_immutable_paths(ann, path))
    return paths


# Derived from field-level annotations at import time — no manual maintenance required.
IMMUTABLE_PATHS: dict[str, str] = _collect_immutable_paths(NetEngineSpec)

# Phase dependency order for applying diffs
DIFF_APPLY_ORDER = [
    "metadata",
    "substrate",
    "dns",
    "pki",
    "identity_platform",
    "world_registry",
    "domain_registry",
    "identity_inworld",
    "ands",
    "world_services",
    "org_apps",
    "gateway_portal",
    "operator",
]


@dataclass
class DiffEntry:
    section: str
    change_type: str  # "added" | "removed" | "updated"
    detail: str


@dataclass
class ReloadResult:
    success: bool
    applied: list[DiffEntry]
    rejected: list[DiffEntry]
    errors: list[str]
    immutability_violations: list[str]


def _get_nested(d: dict[str, Any], path: str) -> Any:
    """Walk a dot-separated path into a nested dict, returning None if missing."""
    keys = path.split(".")
    current: Any = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def check_immutability(old_spec: NetEngineSpec, new_spec: NetEngineSpec) -> list[str]:
    """Return list of violation messages if any immutable paths changed."""
    old = old_spec.model_dump()
    new = new_spec.model_dump()
    violations: list[str] = []
    for path, reason in IMMUTABLE_PATHS.items():
        old_val = _get_nested(old, path)
        new_val = _get_nested(new, path)
        if old_val != new_val:
            violations.append(f"{path}: {reason}")
    return violations


def compute_diff(old_spec: NetEngineSpec, new_spec: NetEngineSpec) -> list[DiffEntry]:
    """Compute a list of diff entries between two specs."""
    old = old_spec.model_dump()
    new = new_spec.model_dump()
    entries: list[DiffEntry] = []

    for section in DIFF_APPLY_ORDER:
        old_sec = old.get(section)
        new_sec = new.get(section)
        if old_sec == new_sec:
            continue
        if old_sec is None:
            entries.append(
                DiffEntry(section=section, change_type="added", detail=f"Section {section} added")
            )
        elif new_sec is None:
            entries.append(
                DiffEntry(
                    section=section, change_type="removed", detail=f"Section {section} removed"
                )
            )
        else:
            # Drill into sub-keys for finer-grained entries
            if isinstance(old_sec, dict) and isinstance(new_sec, dict):
                all_keys = set(old_sec) | set(new_sec)
                for k in sorted(all_keys):
                    ov, nv = old_sec.get(k), new_sec.get(k)
                    if ov == nv:
                        continue
                    if ov is None:
                        entries.append(
                            DiffEntry(
                                section=section, change_type="added", detail=f"{section}.{k} added"
                            )
                        )
                    elif nv is None:
                        entries.append(
                            DiffEntry(
                                section=section,
                                change_type="removed",
                                detail=f"{section}.{k} removed",
                            )
                        )
                    else:
                        entries.append(
                            DiffEntry(
                                section=section,
                                change_type="updated",
                                detail=f"{section}.{k} updated",
                            )
                        )
            else:
                entries.append(
                    DiffEntry(section=section, change_type="updated", detail=f"{section} updated")
                )

    return entries


def diff_orgs(old_spec: NetEngineSpec, new_spec: NetEngineSpec) -> tuple[list[str], list[str]]:
    """Return (added_org_names, removed_org_names) between two specs."""
    old_orgs = {o.name for o in old_spec.world_registry.organizations}
    new_orgs = {o.name for o in new_spec.world_registry.organizations}
    return sorted(new_orgs - old_orgs), sorted(old_orgs - new_orgs)


async def apply_reload(
    old_spec: NetEngineSpec,
    new_spec: NetEngineSpec,
    runtime_state: RuntimeState,
    is_ephemeral: bool = True,
) -> ReloadResult:
    """Apply a spec diff to the running world.

    Checks immutability first — rejects entirely on violation.
    Then applies each diff entry in dependency order.
    Partial failure: halts at failing step; completed steps are NOT rolled back.
    All handlers are expected to be idempotent.
    """
    # 1. Immutability guard — runs before any handler
    violations = check_immutability(old_spec, new_spec)
    if violations:
        return ReloadResult(
            success=False,
            applied=[],
            rejected=[],
            errors=[],
            immutability_violations=violations,
        )

    diff = compute_diff(old_spec, new_spec)
    if not diff:
        return ReloadResult(
            success=True,
            applied=[],
            rejected=[],
            errors=["No changes detected"],
            immutability_violations=[],
        )

    applied: list[DiffEntry] = []
    rejected: list[DiffEntry] = []
    errors: list[str] = []

    # Persistent-mode safety guards
    if not is_ephemeral:
        for entry in diff:
            if entry.section == "pki":
                rejected.append(entry)
                errors.append(f"PKI reconfiguration refused in persistent mode: {entry.detail}")
            elif entry.change_type == "removed" and entry.section in (
                "world_registry",
                "identity_inworld",
            ):
                rejected.append(entry)
                errors.append("Org removal refused in persistent mode: use explicit API call")
        if rejected:
            return ReloadResult(
                success=False,
                applied=[],
                rejected=rejected,
                errors=errors,
                immutability_violations=[],
            )

    # 2. Apply diff entries in order
    from netengine.handlers.context import PhaseContext
    from netengine.phases.phase_ands import ANDsPhaseHandler
    from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
    from netengine.phases.phase_registries import RegistriesPhaseHandler

    context = PhaseContext(spec=new_spec, runtime_state=runtime_state, logger=logger)

    for entry in diff:
        try:
            if (
                entry.section in ("world_registry", "identity_inworld")
                and entry.change_type == "added"
            ):
                # New org — run registries + identity phases for added orgs
                if entry.section == "world_registry":
                    await RegistriesPhaseHandler().execute(context)
                elif entry.section == "identity_inworld":
                    await InWorldIdentityPhaseHandler().execute(context)
            elif entry.section == "ands":
                await ANDsPhaseHandler().execute(context)
            # Other sections: log as applied without re-running a full phase
            # (DNS, PKI, services changes may need targeted handler calls added here)
            applied.append(entry)
            logger.info(f"Reload applied: {entry.detail}")
        except Exception as exc:
            errors.append(f"Failed applying {entry.detail}: {exc}")
            rejected.append(entry)
            logger.error(f"Reload halted at {entry.detail}: {exc}")
            break

    # Persist updated spec into runtime state
    if applied:
        runtime_state.world_spec = new_spec.model_dump()
        runtime_state.save()

    return ReloadResult(
        success=len(rejected) == 0,
        applied=applied,
        rejected=rejected,
        errors=errors,
        immutability_violations=[],
    )
