"""Post-boot diagnostic runner — orchestrates world-health probes concurrently.

These diagnostics require a loaded ``NetEngineSpec`` and validate a configured
or running world. Host-readiness checks for ``netengine doctor`` live in
:mod:`netengine.diagnostic.preflight` and run before any spec is loaded.
"""

import asyncio
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Coroutine

from netengine.spec.models import NetEngineSpec


class ProbeStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(init=False)
class ProbeResult:
    name: str
    status: ProbeStatus
    detail: str
    hint: str | None
    elapsed_ms: float | None
    remediation: str | None
    related_phase: int | None
    related_resource: str | None
    related_logs: list[str]
    command_to_retry: str | None

    def __init__(
        self,
        name: str,
        status: ProbeStatus,
        detail: str,
        hint: str | None = None,
        elapsed_ms: float | None = None,
        remediation: str | None = None,
        related_phase: int | None = None,
        related_resource: str | None = None,
        related_logs: list[str] | None = None,
        command_to_retry: str | None = None,
        *,
        phase: int | None = None,
        resource: str | None = None,
        logs: list[str] | None = None,
        retry_command: str | None = None,
    ) -> None:
        """Create a probe result, accepting legacy aliases for actionable fields."""
        self.name = name
        self.status = status
        self.detail = detail
        self.hint = hint
        self.elapsed_ms = elapsed_ms
        self.remediation = remediation or hint
        self.related_phase = related_phase if related_phase is not None else phase
        self.related_resource = related_resource if related_resource is not None else resource
        log_commands = related_logs if related_logs is not None else logs
        self.related_logs = list(log_commands or [])
        self.command_to_retry = command_to_retry if command_to_retry is not None else retry_command

    @property
    def phase(self) -> int | None:
        """Backward-compatible alias for ``related_phase``."""
        return self.related_phase

    @phase.setter
    def phase(self, value: int | None) -> None:
        self.related_phase = value

    @property
    def resource(self) -> str | None:
        """Backward-compatible alias for ``related_resource``."""
        return self.related_resource

    @resource.setter
    def resource(self, value: str | None) -> None:
        self.related_resource = value

    @property
    def logs(self) -> list[str]:
        """Backward-compatible alias for ``related_logs``."""
        return self.related_logs

    @logs.setter
    def logs(self, value: list[str]) -> None:
        self.related_logs = value

    @property
    def retry_command(self) -> str | None:
        """Backward-compatible alias for ``command_to_retry``."""
        return self.command_to_retry

    @retry_command.setter
    def retry_command(self, value: str | None) -> None:
        self.command_to_retry = value


ProbeFunc = Callable[[NetEngineSpec], Coroutine[Any, Any, ProbeResult]]


class DiagnosticRunner:
    def __init__(self, spec: NetEngineSpec) -> None:
        self._spec = spec
        self._probes: list[ProbeFunc] = []

    def register(self, fn: ProbeFunc) -> None:
        self._probes.append(fn)

    async def run(self) -> list[ProbeResult]:
        tasks = [self._timed(fn) for fn in self._probes]
        results: list[ProbeResult] = await asyncio.gather(*tasks, return_exceptions=False)
        return results

    async def _timed(self, fn: ProbeFunc) -> ProbeResult:
        import time

        start = time.monotonic()
        try:
            result = await fn(self._spec)
        except Exception as exc:
            result = ProbeResult(
                name=_probe_name(fn),
                status=ProbeStatus.FAIL,
                detail=f"Probe crashed: {exc}",
                hint="Check netengine logs for details.",
                remediation="Inspect NetEngine logs and fix the crashing probe before retrying.",
                related_logs=["netengine logs"],
                command_to_retry="netengine diagnose",
            )
        elapsed = (time.monotonic() - start) * 1000
        result.elapsed_ms = elapsed
        _apply_probe_defaults(result, fn)
        return result


def _probe_module(fn: ProbeFunc) -> Any:
    """Return the imported module that owns a probe function."""
    module = sys.modules.get(fn.__module__)
    if module is None:
        module = __import__(fn.__module__, fromlist=["dummy"])
    return module


def _probe_name(fn: ProbeFunc) -> str:
    """Return the display name declared by the probe module, falling back to the function name."""
    module = _probe_module(fn)
    return getattr(module, "_PROBE_NAME", getattr(fn, "__probe_name__", fn.__name__))


def _apply_probe_defaults(result: ProbeResult, fn: ProbeFunc) -> None:
    """Populate phase/resource/log/retry fields from probe module defaults.

    Individual probes may override these fields when a result points to a more
    specific resource, log stream, or retry action.
    """
    module = _probe_module(fn)
    if result.related_phase is None:
        result.related_phase = getattr(module, "_PHASE", None)
    if result.related_resource is None:
        result.related_resource = getattr(module, "_RESOURCE", None)
    if not result.related_logs:
        result.related_logs = list(getattr(module, "_LOGS", []))
    if result.command_to_retry is None:
        result.command_to_retry = getattr(module, "_RETRY", None)
    if result.remediation is None and result.hint:
        result.remediation = result.hint
    if result.command_to_retry is None and result.related_phase is not None:
        result.command_to_retry = f"netengine heal --phase {result.related_phase}"
    if not result.related_logs and result.related_resource:
        result.related_logs = ["netengine logs"]


def build_runner(spec: NetEngineSpec) -> DiagnosticRunner:
    """Build a DiagnosticRunner with all standard probes registered."""
    from netengine.diagnostic.probes import (
        acme,
        dns,
        events,
        mail,
        network,
        oidc,
        pki,
        storage,
        whois,
    )

    runner = DiagnosticRunner(spec)
    for probe_fn in [
        dns.probe,
        pki.probe,
        acme.probe,
        oidc.probe,
        network.probe,
        mail.probe,
        storage.probe,
        whois.probe,
        events.probe,
    ]:
        runner.register(probe_fn)
    return runner
