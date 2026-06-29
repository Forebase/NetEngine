"""Post-boot diagnostic runner — orchestrates world-health probes concurrently.

These diagnostics require a loaded ``NetEngineSpec`` and validate a configured
or running world. Host-readiness checks for ``netengine doctor`` live in
:mod:`netengine.diagnostic.preflight` and run before any spec is loaded.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from netengine.spec.models import NetEngineSpec


class ProbeStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class ProbeResult:
    name: str
    status: ProbeStatus
    detail: str
    hint: str | None = None
    elapsed_ms: float | None = None
    remediation: str | None = None
    phase: int | None = None
    resource: str | None = None
    logs: list[str] = field(default_factory=list)
    retry_command: str | None = None

    def __post_init__(self) -> None:
        """Keep legacy hints useful as remediation for actionable diagnostics."""
        if self.remediation is None and self.hint:
            self.remediation = self.hint


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
                name=getattr(fn, "__probe_name__", fn.__name__),
                status=ProbeStatus.FAIL,
                detail=f"Probe crashed: {exc}",
                hint="Check netengine logs for details.",
                remediation="Inspect NetEngine logs and fix the crashing probe before retrying.",
                logs=["netengine logs"],
                retry_command="netengine diagnose",
            )
        elapsed = (time.monotonic() - start) * 1000
        result.elapsed_ms = elapsed
        _apply_probe_defaults(result, fn)
        return result


def _apply_probe_defaults(result: ProbeResult, fn: ProbeFunc) -> None:
    """Populate phase/resource/log/retry fields from probe module defaults.

    Individual probes may override these fields when a result points to a more
    specific resource, log stream, or retry action.
    """
    module = __import__(fn.__module__, fromlist=["dummy"])
    if result.phase is None:
        result.phase = getattr(module, "_PHASE", None)
    if result.resource is None:
        result.resource = getattr(module, "_RESOURCE", None)
    if not result.logs:
        result.logs = list(getattr(module, "_LOGS", []))
    if result.retry_command is None:
        result.retry_command = getattr(module, "_RETRY", None)
    if result.remediation is None and result.hint:
        result.remediation = result.hint
    if result.retry_command is None and result.phase is not None:
        result.retry_command = f"netengine heal --phase {result.phase}"
    if not result.logs and result.resource:
        result.logs = ["netengine logs"]
    return result


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
