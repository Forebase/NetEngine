# netengine/workers/pki_cert_rotation_worker.py
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from netengine.core.pgmq_client import PGMQClient
from netengine.core.state import RuntimeState
from netengine.events.queues import Queue
from netengine.events.schema import EventEnvelope
from netengine.handlers.pki_handler import PKIHandler

logger = logging.getLogger(__name__)

# Built-in cert types always managed by the rotation worker.
_BUILTIN_CERT_TYPES = ["platform_identity", "inworld_identity", "app", "storage"]


@dataclass
class CertTypeRotationConfig:
    """Configuration for a certificate type (e.g., "app", "platform_identity")."""

    cert_type: str
    rotation_interval_hours: int = 24
    expiry_warning_days: int = 30
    rotation_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None


class PKICertRotationWorker:
    """Background worker that monitors and rotates expiring certificates."""

    def __init__(
        self,
        pki_handler: PKIHandler,
        pgmq: PGMQClient,
        cert_type_configs: List[CertTypeRotationConfig],
    ):
        self.pki_handler = pki_handler
        self.pgmq = pgmq
        # Initial configs, keyed by cert_type. Used as fallback if spec reload fails.
        self._initial_configs: Dict[str, CertTypeRotationConfig] = {
            cfg.cert_type: cfg for cfg in cert_type_configs
        }
        # Per-cert-type callbacks survive spec reloads (they're in-process callables).
        self._callbacks: Dict[str, Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]]] = {
            cfg.cert_type: cfg.rotation_callback for cfg in cert_type_configs
        }
        self.logger = logging.getLogger(__name__)

    def _resolve_configs(self, state: RuntimeState) -> Dict[str, CertTypeRotationConfig]:
        """Return the current rotation config, refreshed from world_spec if available.

        On live-reload the world_spec in RuntimeState is updated; re-reading it here
        means the worker picks up rotation_policy changes without a restart.
        Falls back to the initial configs on any parse error.
        """
        if not state.world_spec:
            return self._initial_configs

        try:
            from netengine.spec.models import NetEngineSpec

            spec = NetEngineSpec(**state.world_spec)
            policy = spec.pki.rotation_policy

            if not policy.enabled:
                return {}

            extra_types = [t for t in policy.cert_type_overrides if t not in _BUILTIN_CERT_TYPES]
            all_cert_types = _BUILTIN_CERT_TYPES + extra_types

            configs: Dict[str, CertTypeRotationConfig] = {}
            for cert_type in all_cert_types:
                override = policy.cert_type_overrides.get(cert_type)
                if isinstance(override, dict):
                    interval = override.get(
                        "rotation_interval_hours", policy.default_interval_hours
                    )
                    warning = override.get("expiry_warning_days", policy.default_warning_days)
                else:
                    interval = policy.default_interval_hours
                    warning = policy.default_warning_days

                configs[cert_type] = CertTypeRotationConfig(
                    cert_type=cert_type,
                    rotation_interval_hours=interval,
                    expiry_warning_days=warning,
                    rotation_callback=self._callbacks.get(cert_type),
                )
            return configs
        except Exception as exc:
            self.logger.warning(
                f"pki_rotation_worker: spec reload failed, using cached config: {exc}"
            )
            return self._initial_configs

    async def run(self) -> None:
        """Main worker loop: check expiry per cert type, rotate if needed."""
        while True:
            try:
                state = RuntimeState.load()

                # Re-resolve configs from current spec on every iteration so that
                # live reloads changing rotation_policy take effect without restart.
                current_configs = self._resolve_configs(state)

                # Check each cert type on its own schedule
                for cert_type, config in current_configs.items():
                    last_check = self._get_last_check_time(state, cert_type)
                    if self._should_check_now(last_check, config.rotation_interval_hours):
                        await self._check_and_rotate_cert_type(state, cert_type, config)
                        self._update_last_check_time(state, cert_type)

                state.save()

                # Sleep for a reasonable interval (1 hour cap to refresh state)
                await asyncio.sleep(3600)
            except Exception as e:
                self.logger.error("pki_rotation_worker_error", extra={"error": str(e)})
                await asyncio.sleep(300)  # Backoff on error

    def _get_last_check_time(self, state: RuntimeState, cert_type: str) -> Optional[datetime]:
        """Get the last check time for a certificate type."""
        if not state.pki_rotation_state:
            return None
        last_check_by_type = state.pki_rotation_state.get("last_check_by_type", {})
        last_check = last_check_by_type.get(cert_type)
        if isinstance(last_check, str):
            return datetime.fromisoformat(last_check)
        if isinstance(last_check, datetime):
            return last_check
        return None

    def _should_check_now(
        self, last_check: Optional[datetime], rotation_interval_hours: int
    ) -> bool:
        """Determine if it's time to check this cert type."""
        if last_check is None:
            return True
        next_check = last_check + timedelta(hours=rotation_interval_hours)
        return datetime.now(UTC) >= next_check

    def _update_last_check_time(self, state: RuntimeState, cert_type: str) -> None:
        """Update the last check time for a certificate type."""
        if not state.pki_rotation_state:
            state.pki_rotation_state = {}
        if "last_check_by_type" not in state.pki_rotation_state:
            state.pki_rotation_state["last_check_by_type"] = {}
        state.pki_rotation_state["last_check_by_type"][cert_type] = datetime.now(UTC)

    async def _check_and_rotate_cert_type(
        self, state: RuntimeState, cert_type: str, config: CertTypeRotationConfig
    ) -> None:
        """Check tracked certificates of a type and rotate those expiring within threshold."""
        now = datetime.now(UTC)
        warning_threshold = now + timedelta(days=config.expiry_warning_days)

        for cn, cert_metadata in state.issued_certificates.items():
            if cert_metadata.get("cert_type") != cert_type:
                continue

            expires_at_str = cert_metadata.get("expires_at")
            if isinstance(expires_at_str, str):
                expires_at = datetime.fromisoformat(expires_at_str)
            elif isinstance(expires_at_str, datetime):
                expires_at = expires_at_str
            else:
                continue

            if expires_at <= warning_threshold:
                self.logger.info(
                    "certificate_rotation_needed",
                    extra={
                        "cn": cn,
                        "cert_type": cert_type,
                        "expires_in_days": (expires_at - now).days,
                    },
                )

                try:
                    # Call rotation callback if present (for graceful transition prep)
                    if config.rotation_callback:
                        await config.rotation_callback(cn, cert_metadata)

                    # Re-issue certificate with incremented version
                    sans = cert_metadata.get("sans", [])
                    cert_pem, key_pem = await self.pki_handler.issue_cert(cn, sans)

                    # Update metadata with new version and expiry
                    new_expiry = self.pki_handler.extract_cert_expiry(cert_pem)
                    new_version = cert_metadata.get("version", 1) + 1

                    cert_metadata["issued_at"] = datetime.now(UTC).isoformat()
                    cert_metadata["expires_at"] = new_expiry.isoformat()
                    cert_metadata["rotated_at"] = datetime.now(UTC).isoformat()
                    cert_metadata["version"] = new_version

                    # Emit event for monitoring
                    await self._emit_rotation_event(
                        cn, cert_type, "success", new_expiry, new_version
                    )

                    self.logger.info(
                        "certificate_rotated",
                        extra={
                            "cn": cn,
                            "cert_type": cert_type,
                            "new_version": new_version,
                            "new_expiry": new_expiry.isoformat(),
                        },
                    )
                except Exception as e:
                    self.logger.error(
                        "certificate_rotation_failed",
                        extra={"cn": cn, "cert_type": cert_type, "error": str(e)},
                    )
                    await self._emit_rotation_event(cn, cert_type, "failed", error=str(e))

    async def _emit_rotation_event(
        self,
        cn: str,
        cert_type: str,
        status: str,
        expiry_date: Optional[datetime] = None,
        version: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """Emit event to PGMQ for monitoring/logging."""
        payload = {
            "cn": cn,
            "cert_type": cert_type,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
            "expires_at": expiry_date.isoformat() if expiry_date else None,
            "version": version,
            "error": error,
        }
        try:
            event = EventEnvelope.create(
                event_type="pki.certificate_rotation",
                emitted_by="pki_cert_rotation_worker",
                payload=payload,
            )
            await self.pgmq.send(Queue.PKI_CERT_ROTATION_EVENTS, event)
        except Exception as e:
            self.logger.debug(f"Failed to emit rotation event: {e}")
