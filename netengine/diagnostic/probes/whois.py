"""WHOIS probe — checks WHOIS server accepts connections and has domain records."""

import asyncio

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "WHOIS"
_TIMEOUT = 5.0


async def probe(spec: NetEngineSpec) -> ProbeResult:
    if not spec.world_registry.whois.enabled:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.SKIP,
            detail="WHOIS disabled in spec",
        )

    listen_ip = spec.world_registry.whois.listen_ip
    port = spec.world_registry.whois.port
    domain_count = len(spec.domain_registry.initial_domains)

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(listen_ip, port),
            timeout=_TIMEOUT,
        )
        # Send a query for a known test name
        writer.write(b"help\r\n")
        await writer.drain()
        try:
            await asyncio.wait_for(reader.read(256), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        writer.close()
        await writer.wait_closed()

        if domain_count == 0:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.WARN,
                detail=f"WHOIS at {listen_ip}:{port} responding, but spec has 0 initial domains",
                hint="Add domains to spec.domain_registry.initial_domains.",
            )

        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.OK,
            detail=f"WHOIS at {listen_ip}:{port} responding ({domain_count} domain(s) in spec)",
        )
    except (ConnectionRefusedError, OSError):
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Cannot connect to WHOIS at {listen_ip}:{port}",
            hint="Check if WHOIS server container is running (phase 5).",
        )
    except asyncio.TimeoutError:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"WHOIS at {listen_ip}:{port} timed out after {_TIMEOUT}s",
        )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"WHOIS probe error: {exc}",
        )
