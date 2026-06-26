"""DNS probe — checks root and platform zone nameservers are responding."""

import asyncio
import socket

import dns.resolver
import dns.exception

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "DNS"


async def probe(spec: NetEngineSpec) -> ProbeResult:
    root_ip = spec.dns.root.listen_ip
    platform_ip = spec.dns.platform_zone.listen_ip
    platform_zone = spec.dns.platform_zone.name

    # Run blocking DNS calls in executor to stay async-friendly
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _query_soa, root_ip, ".")
        if not result:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.FAIL,
                detail=f"Root DNS at {root_ip}:53 did not return SOA for '.'",
                hint=f"Run: netengine status — check phase 1 completed",
            )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Root DNS at {root_ip}:53 unreachable: {exc}",
            hint="Check if CoreDNS container is running.",
        )

    try:
        result2 = await loop.run_in_executor(None, _query_soa, platform_ip, platform_zone)
        if not result2:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.WARN,
                detail=f"Root DNS OK, but platform zone {platform_zone!r} SOA not found at {platform_ip}",
                hint="Check phase 1 logs for platform zone registration.",
            )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.WARN,
            detail=f"Root DNS OK, platform zone {platform_zone!r} at {platform_ip} unreachable: {exc}",
            hint="Check if platform zone container is running.",
        )

    return ProbeResult(
        name=_PROBE_NAME,
        status=ProbeStatus.OK,
        detail=f"Root DNS ({root_ip}) and platform zone {platform_zone!r} ({platform_ip}) responding",
    )


def _query_soa(server_ip: str, zone: str) -> bool:
    """Send a SOA query to server_ip:53 for zone. Returns True if answer received."""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server_ip]
    resolver.port = 53
    resolver.lifetime = 5.0
    try:
        resolver.resolve(zone, "SOA")
        return True
    except dns.resolver.NoAnswer:
        # Server responded but zone has no SOA record — still means DNS is up
        return True
    except dns.exception.DNSException:
        return False
