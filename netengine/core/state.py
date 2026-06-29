import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, TypedDict, cast

from netengine.logging import get_logger

if TYPE_CHECKING:
    import asyncio

logger = get_logger(__name__)

DEFAULT_STATE_FILE = "netengines_state.json"


JsonPrimitive = str | int | float | bool | None
JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


class EventSendFailure(TypedDict):
    event_type: str
    queue: str
    emitted_by: str
    exception: str
    event_id: str
    correlation_id: str | None
    parent_event_id: str | None
    failed_at: str


class PhaseOutputBase(TypedDict, total=False):
    deployed_at: str
    event_send_failures: list[EventSendFailure]


class OrchestratorOutput(TypedDict, total=False):
    type: str
    status: str
    healthy: bool
    version: str
    initialized_at: str


class NetworkOutput(TypedDict, total=False):
    id: str
    name: str
    cidr: str
    gateway: str
    driver: str


class GatewayOutput(TypedDict, total=False):
    networks: dict[str, str]
    ip_addresses: dict[str, str]
    healthy: bool
    status: str


class NTPOutput(TypedDict, total=False):
    enabled: bool
    servers: list[str]
    synchronized: bool
    status: str


class SubstrateOutput(PhaseOutputBase, total=False):
    orchestrator: OrchestratorOutput
    networks: dict[str, NetworkOutput]
    gateway: GatewayOutput
    ntp: NTPOutput


class DNSZoneOutput(TypedDict, total=False):
    name: str
    soa: str
    records: list[dict[str, JsonValue]]
    listen_ip: str
    healthy: bool


class TLDOutput(TypedDict, total=False):
    name: str
    listen_ip: str
    records: list[dict[str, JsonValue]]


class DNSOutput(PhaseOutputBase, total=False):
    root_zone: DNSZoneOutput
    platform_zone: DNSZoneOutput
    tlds: dict[str, TLDOutput]
    zone_files: dict[str, str]
    coredns_container_id: str
    healthy: bool


class PKIOutput(PhaseOutputBase, total=False):
    ca_ip: str
    ca_dns: str
    container_id: str | None
    bootstrapped: bool
    mock: bool
    crl_url: str
    crl_enabled: bool
    ocsp_url: str
    ocsp_enabled: bool
    intermediate_ca_enabled: bool
    intermediate_ca_cert_available: bool
    intermediate_ca_cert: str
    dnssec_enabled: bool
    dnssec_zone: str
    dnssec_ksk: str
    dnssec_zsk: str


class IdentityPlatformOutput(PhaseOutputBase, total=False):
    keycloak_container_id: str
    platform_realm_id: str
    admin_user_id: str
    platform_client_id: str
    platform_client_auth_id: str
    platform_client_secret: str


class WorldRegistryOutput(PhaseOutputBase, total=False):
    seeded: bool


class DomainRegistryOutput(PhaseOutputBase, total=False):
    address_pools_seeded: bool
    tld_delegations: list[dict[str, JsonValue]]


class GenericPhaseOutput(PhaseOutputBase, total=False):
    status: str
    healthy: bool


class DriftHistoryEvent(TypedDict):
    phase_num: int
    detected_at: str
    healed_at: str | None
    healing_failed: bool
    error: str | None


class IssuedCertificateMetadata(TypedDict):
    cert_type: str
    issued_at: str | datetime
    expires_at: str | datetime
    sans: list[str]
    rotated_at: str | datetime | None
    version: int


class PKIRotationState(TypedDict, total=False):
    last_check_by_type: dict[str, str | datetime]


def get_state_file() -> Path:
    """Return the runtime state file path for the current environment."""
    return Path(os.environ.get("NETENGINE_STATE_FILE", DEFAULT_STATE_FILE))


@dataclass
class RuntimeState:
    """Mutable runtime state, persisted to a local JSON file between phases."""

    # Execution trace
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None

    # Phase completion tracking
    phase_completed: Dict[str, bool] = field(default_factory=dict)

    # Phase outputs (one dict per phase, populated on completion)
    substrate_output: Optional[SubstrateOutput] = None
    dns_output: Optional[DNSOutput] = None
    pki_output: Optional[PKIOutput] = None
    identity_platform_output: Optional[IdentityPlatformOutput] = None
    world_registry_output: Optional[WorldRegistryOutput] = None
    domain_registry_output: Optional[DomainRegistryOutput] = None
    identity_inworld_output: Optional[GenericPhaseOutput] = None
    ands_output: Optional[GenericPhaseOutput] = None
    world_services_output: Optional[GenericPhaseOutput] = None
    org_apps_output: Optional[GenericPhaseOutput] = None

    # Legacy container ID fields (kept for backward compat with existing handlers)
    gateway_container_id: Optional[str] = None
    dns_root_container_id: Optional[str] = None
    ca_cert_pem: Optional[str] = None
    step_ca_container_id: Optional[str] = None
    pki_bootstrapped: bool = False
    keycloak_platform_container_id: Optional[str] = None
    platform_realm_id: Optional[str] = None
    admin_user_id: Optional[str] = None
    inworld_keycloak_container_id: Optional[str] = None
    inworld_admin_password: Optional[str] = None
    world_spec: Optional[Dict[str, Any]] = None
    bootstrap_admin_password: Optional[str] = None
    platform_client_id: Optional[str] = None
    platform_client_auth_id: Optional[str] = None
    platform_client_secret: Optional[str] = None

    # Drift detection and self-healing
    drift_history: list[DriftHistoryEvent] = field(default_factory=list)
    last_drift_check_at: Optional[datetime] = None
    current_drift_phases: list[int] = field(default_factory=list)
    # PKI certificate rotation tracking
    issued_certificates: Dict[str, IssuedCertificateMetadata] = field(default_factory=dict)
    pki_rotation_state: PKIRotationState = field(default_factory=lambda: cast(PKIRotationState, {}))

    # Extended PKI state
    intermediate_ca_cert: Optional[str] = None
    dnssec_output: Optional[Dict[str, Any]] = None

    # Gateway portal state
    gateway_portal_output: Optional[Dict[str, Any]] = None

    # Recent structured failures from best-effort PGMQ event emission.
    event_send_failures: list[EventSendFailure] = field(default_factory=list)

    @classmethod
    def load(cls) -> "RuntimeState":
        state_file = get_state_file()
        if state_file.exists():
            with open(state_file, "r") as f:
                data: Dict[str, Any] = json.load(f)
            # datetime fields are stored as ISO strings
            for dt_field in ("started_at", "completed_at", "last_error_at", "last_drift_check_at"):
                if data.get(dt_field):
                    data[dt_field] = datetime.fromisoformat(data[dt_field])
            for dt_field in ("started_at", "completed_at", "last_error_at"):
                dt_value = data.get(dt_field)
                if dt_value and isinstance(dt_value, str):
                    data[dt_field] = datetime.fromisoformat(dt_value)

            # Deserialize datetime strings in certificate metadata
            if data.get("issued_certificates"):
                for cn, cert_metadata in data["issued_certificates"].items():
                    if isinstance(cert_metadata, dict):
                        for dt_field in ("issued_at", "expires_at", "rotated_at"):
                            dt_value = cert_metadata.get(dt_field)
                            if dt_value and isinstance(dt_value, str):
                                cert_metadata[dt_field] = datetime.fromisoformat(dt_value)

            # Deserialize datetime strings in pki_rotation_state
            if data.get("pki_rotation_state"):
                last_check_by_type = data["pki_rotation_state"].get("last_check_by_type")
                if last_check_by_type and isinstance(last_check_by_type, dict):
                    for cert_type, last_check in last_check_by_type.items():
                        if last_check and isinstance(last_check, str):
                            last_check_by_type[cert_type] = datetime.fromisoformat(last_check)

            state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            state._discard_completion_flags_without_outputs()
            return state
        return cls()

    def _discard_completion_flags_without_outputs(self) -> None:
        """Ignore completion flags that do not have their matching phase output."""
        phase_outputs = {
            "0": (self.substrate_output,),
            "1": (self.dns_output,),
            "2": (self.dns_output,),
            "3": (self.pki_output,),
            "4": (self.identity_platform_output,),
            "5": (self.world_registry_output, self.domain_registry_output),
            "6": (self.identity_inworld_output,),
            "7": (self.ands_output,),
            "8": (self.world_services_output,),
            "9": (self.org_apps_output,),
        }
        for phase, outputs in phase_outputs.items():
            if self.phase_completed.get(phase) is True and any(output is None for output in outputs):
                self.phase_completed.pop(phase, None)

        if self.pki_output is None and self.phase_completed.get("3") is True:
            self.phase_completed.pop("3", None)

    def save(self) -> None:
        self._discard_completion_flags_without_outputs()
        data = asdict(self)
        # Serialize datetime fields to ISO strings
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()

        # Serialize nested datetime objects in certificate metadata
        if data.get("issued_certificates"):
            for cn, cert_metadata in data["issued_certificates"].items():
                for dt_field in ("issued_at", "expires_at", "rotated_at"):
                    if isinstance(cert_metadata.get(dt_field), datetime):
                        cert_metadata[dt_field] = cert_metadata[dt_field].isoformat()

        # Serialize nested datetime objects in pki_rotation_state
        if data.get("pki_rotation_state"):
            last_check_by_type = data["pki_rotation_state"].get("last_check_by_type")
            if last_check_by_type:
                for cert_type, last_check in last_check_by_type.items():
                    if isinstance(last_check, datetime):
                        last_check_by_type[cert_type] = last_check.isoformat()

        state_file = get_state_file()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to a unique temporary file in the same directory,
        # flush it to disk, then replace the target in one filesystem operation.
        # 0o600 permissions protect plaintext secrets stored in the state file.
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=state_file.parent,
                prefix=f"{state_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                os.chmod(tmp_path, 0o600)
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(state_file)
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

    def sync_to_supabase(self) -> Optional["asyncio.Task[None]"]:
        """Best-effort sync of the local JSON state snapshot to Supabase.

        The local JSON state file remains the authoritative runtime state. The Supabase
        ``runtime_state`` row is only an audit/convenience mirror, so sync failures are
        logged at debug level and do not interrupt orchestration. When called from a
        running event loop, the sync is scheduled in the background and the created task
        is returned so async callers may optionally await or inspect it.
        """
        try:
            import asyncio

            from netengine.core.supabase_client import get_db

            data = asdict(self)
            for k, v in data.items():
                if isinstance(v, datetime):
                    data[k] = v.isoformat()

            async def _sync() -> None:
                db = await get_db()
                await db.table("runtime_state").upsert(
                    {"key": "current", "value": json.dumps(data)}
                ).execute()

            def _log_sync_task_exception(task: "asyncio.Task[None]") -> None:
                try:
                    task.result()
                except asyncio.CancelledError:
                    logger.debug("State DB sync cancelled")
                except Exception as exc:
                    logger.debug(f"State DB sync skipped: {exc}")

            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside an async context — schedule as best-effort background work,
                # but still observe task failures so exceptions are not lost.
                task = loop.create_task(_sync())
                task.add_done_callback(_log_sync_task_exception)
                return task

            loop.run_until_complete(_sync())
            return None
        except Exception as exc:
            logger.debug(f"State DB sync skipped: {exc}")
            return None
