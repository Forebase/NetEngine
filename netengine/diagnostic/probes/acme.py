"""ACME probe — checks step-ca ACME directory endpoint."""

import aiohttp

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "ACME"
_TIMEOUT = aiohttp.ClientTimeout(total=5)
_PHASE = 3
_RESOURCE = "step-ca ACME directory"
_LOGS = ["docker logs netengines_step_ca"]
_RETRY = "netengine heal --phase 3"


async def probe(spec: NetEngineSpec) -> ProbeResult:
    listen_ip = spec.pki.acme.listen_ip
    # step-ca ACME directory is served on port 9000 by default
    url = f"http://{listen_ip}:9000/acme/acme/directory"

    if not spec.pki.acme.enabled:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.SKIP,
            detail="ACME disabled in spec",
        )

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, timeout=_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return ProbeResult(
                        name=_PROBE_NAME,
                        status=ProbeStatus.OK,
                        detail=f"ACME directory at {url} returned 200 OK",
                    )
                return ProbeResult(
                    name=_PROBE_NAME,
                    status=ProbeStatus.FAIL,
                    detail=f"ACME directory at {url} returned HTTP {resp.status}",
                    hint="Check step-ca container logs.",
                )
    except (aiohttp.ClientError, TimeoutError, OSError):
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Cannot connect to ACME at {url}",
            hint="Check if step-ca container is running (phase 3).",
        )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"ACME probe error: {repr(exc)}",
        )
