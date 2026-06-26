"""OIDC probe — checks Keycloak platform and in-world identity endpoints."""

import aiohttp

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "OIDC"
_TIMEOUT = aiohttp.ClientTimeout(total=8)
_KEYCLOAK_PORT = 8080


async def probe(spec: NetEngineSpec) -> ProbeResult:
    platform_ip = spec.identity_platform.listen_ip
    platform_realm = spec.identity_platform.realm_name
    base_url = f"http://{platform_ip}:{_KEYCLOAK_PORT}"
    health_url = f"{base_url}/health/ready"
    oidc_url = f"{base_url}/realms/{platform_realm}/" ".well-known/openid-configuration"

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, timeout=_TIMEOUT) as session:
            # Health check
            try:
                async with session.get(health_url) as resp:
                    if resp.status not in (200, 204):
                        return ProbeResult(
                            name=_PROBE_NAME,
                            status=ProbeStatus.FAIL,
                            detail=(
                                f"Keycloak /health/ready returned HTTP "
                                f"{resp.status} at {platform_ip}"
                            ),
                            hint="Run: docker logs netengines_keycloak",
                        )
            except (aiohttp.ClientError, TimeoutError, OSError):
                return ProbeResult(
                    name=_PROBE_NAME,
                    status=ProbeStatus.FAIL,
                    detail=f"Cannot connect to Keycloak at {platform_ip}:{_KEYCLOAK_PORT}",
                    hint="Check if Keycloak container is running (phase 4).",
                )

            # OIDC discovery
            try:
                async with session.get(oidc_url) as resp:
                    if resp.status == 200:
                        return ProbeResult(
                            name=_PROBE_NAME,
                            status=ProbeStatus.OK,
                            detail=f"Keycloak healthy, realm '{platform_realm}' OIDC config OK",
                        )
                    return ProbeResult(
                        name=_PROBE_NAME,
                        status=ProbeStatus.WARN,
                        detail=(
                            f"Keycloak healthy but realm '{platform_realm}' "
                            f"OIDC discovery returned {resp.status}"
                        ),
                        hint=f"Realm '{platform_realm}' may not be provisioned yet.",
                    )
            except Exception as exc:
                return ProbeResult(
                    name=_PROBE_NAME,
                    status=ProbeStatus.WARN,
                    detail=f"Keycloak healthy but OIDC discovery failed: {exc}",
                )

    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"OIDC probe error: {exc or repr(exc)}",
        )
