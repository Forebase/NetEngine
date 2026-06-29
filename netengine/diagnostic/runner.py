"""Diagnostic runner — orchestrates all probes concurrently."""

import asyncio
from dataclasses import dataclass
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
            )
        elapsed = (time.monotonic() - start) * 1000
        result.elapsed_ms = elapsed
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
