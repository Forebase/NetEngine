"""Network probe — checks nftables rules and Docker networks exist."""

import asyncio
import subprocess

from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "Network"
_PHASE = 0
_RESOURCE = "Docker networks / nftables"
_LOGS = ["docker network ls", "sudo nft list ruleset"]
_RETRY = "netengine heal --phase 0"


async def probe(spec: NetEngineSpec) -> ProbeResult:
    loop = asyncio.get_running_loop()

    # Check Docker networks
    try:
        import docker

        client = docker.from_env()
        nets = {n.name for n in client.networks.list()}
        expected_prefixes = ("netengine_", "netengines_")
        found = [n for n in nets if any(n.startswith(p) for p in expected_prefixes)]
        if not found:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.WARN,
                detail="No netengine Docker networks found",
                hint="Phase 0 may not have completed — run `netengine up`.",
            )
        network_detail = f"{len(found)} Docker network(s): {', '.join(sorted(found))}"
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Docker unavailable: {exc}",
            hint="Ensure Docker daemon is running.",
        )

    # Check nftables rules for AND isolation (best-effort, may require root)
    and_count = len(spec.ands.instances)
    if and_count == 0:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.OK,
            detail=f"{network_detail}, no ANDs defined",
        )

    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["nft", "list", "ruleset"],
                capture_output=True,
                text=True,
                timeout=5,
            ),
        )
        if result.returncode == 0:
            chain_count = result.stdout.count("chain ")
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.OK,
                detail=(f"{network_detail}, nftables active ({chain_count} chain(s))"),
            )
        else:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.WARN,
                detail=(
                    f"{network_detail}, nftables check failed "
                    f"(may require root): {result.stderr.strip()}"
                ),
                hint="Run `sudo nft list ruleset` to inspect manually.",
            )
    except FileNotFoundError:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.WARN,
            detail=f"{network_detail}, `nft` not found — cannot verify AND isolation rules",
            hint="Install nftables or verify AND rules manually.",
        )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.WARN,
            detail=f"{network_detail}, nftables check error: {exc}",
        )
