"""Mail probe — checks SMTP banner on Postfix."""

import asyncio

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "Mail"
_SMTP_PORT = 25
_TIMEOUT = 5.0


async def probe(spec: NetEngineSpec) -> ProbeResult:
    if not spec.world_services.mail.enabled:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.SKIP,
            detail="Mail disabled in spec",
        )

    listen_ip = spec.world_services.mail.listen_ip

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(listen_ip, _SMTP_PORT),
            timeout=_TIMEOUT,
        )
        banner = await asyncio.wait_for(reader.readline(), timeout=_TIMEOUT)
        writer.close()
        await writer.wait_closed()

        banner_str = banner.decode("ascii", errors="replace").strip()
        if banner_str.startswith("220"):
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.OK,
                detail=f"SMTP at {listen_ip}:{_SMTP_PORT} — {banner_str[:60]}",
            )
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.WARN,
            detail=f"SMTP at {listen_ip}:{_SMTP_PORT} unexpected banner: {banner_str[:60]}",
            hint="Check Postfix container logs.",
        )
    except (ConnectionRefusedError, OSError):
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Cannot connect to SMTP at {listen_ip}:{_SMTP_PORT}",
            hint="Check if Postfix container is running (phase 8).",
        )
    except asyncio.TimeoutError:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"SMTP at {listen_ip}:{_SMTP_PORT} timed out after {_TIMEOUT}s",
            hint="Postfix may be starting up — try again in a moment.",
        )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Mail probe error: {exc}",
        )
