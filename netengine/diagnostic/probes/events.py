"""Event emission diagnostics."""

from netengine.core.state import RuntimeState
from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "Events"


async def probe(spec: NetEngineSpec) -> ProbeResult:
    """Report recent PGMQ event-send failures recorded in runtime state."""
    del spec
    state = RuntimeState.load()
    failures = state.event_send_failures[-10:]
    if not failures:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.OK,
            detail="No recent event-send failures recorded.",
        )

    latest = failures[-1]
    detail = (
        f"{len(failures)} recent event-send failure(s). Latest: "
        f"event_type={latest.get('event_type')}, queue={latest.get('queue')}, "
        f"emitted_by={latest.get('emitted_by')}, event_id={latest.get('event_id')}, "
        f"correlation_id={latest.get('correlation_id')}, exception={latest.get('exception')}"
    )
    return ProbeResult(
        name=_PROBE_NAME,
        status=ProbeStatus.WARN,
        detail=detail,
        hint="Inspect runtime_state.event_send_failures and PGMQ connectivity.",
    )
