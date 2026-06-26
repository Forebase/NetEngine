"""Storage probe — checks MinIO health endpoint."""

import aiohttp

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "Storage"
_MINIO_API_PORT = 9000
_TIMEOUT = aiohttp.ClientTimeout(total=5)


async def probe(spec: NetEngineSpec) -> ProbeResult:
    if not spec.world_services.storage.enabled:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.SKIP,
            detail="Storage disabled in spec",
        )

    listen_ip = spec.world_services.storage.listen_ip
    url = f"http://{listen_ip}:{_MINIO_API_PORT}/minio/health/live"

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, timeout=_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return ProbeResult(
                        name=_PROBE_NAME,
                        status=ProbeStatus.OK,
                        detail=f"MinIO healthy at {listen_ip}:{_MINIO_API_PORT}",
                    )
                return ProbeResult(
                    name=_PROBE_NAME,
                    status=ProbeStatus.FAIL,
                    detail=f"MinIO /minio/health/live returned HTTP {resp.status} at {listen_ip}",
                    hint="Check MinIO container logs.",
                )
    except (aiohttp.ClientError, TimeoutError, OSError):
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Cannot connect to MinIO at {listen_ip}:{_MINIO_API_PORT}",
            hint="Check if MinIO container is running (phase 8).",
        )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Storage probe error: {exc}",
        )
