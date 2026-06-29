"""Cross-world federation end-to-end test: real CoreDNS + peer DNS stub.

Run with:
    pytest tests/integration/test_e2e_federation.py --run-e2e

Requires:
  - Docker daemon accessible on the host
  - The 'core' and 'platform' Docker networks must not already exist

What gets validated:
  - GatewayPortalHandler runs without error in PEERED mode
  - The peer's TLD forwarding stub is appended to the CoreDNS Corefile
  - CoreDNS reloads the new config (SIGHUP sent) without crashing
  - Runtime state records the peer federation output

What is intentionally NOT tested here (requires dedicated infrastructure):
  - nftables routing  (needs a gateway container provisioned by a separate phase)
  - Trust anchor cert (tested via unit tests in test_gateway_portal.py)
  - Actual cross-world DNS resolution (needs a real peer world running)
"""

from __future__ import annotations

import asyncio
import io
import tarfile
from pathlib import Path

import pytest

from netengine.core.state import RuntimeState
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.gateway_portal_handler import GatewayPortalHandler
from netengine.handlers.substrate import SubstrateHandler
from logs import get_logger
from netengine.spec.loader import load_spec
from netengine.spec.models import (
    CrossWorldConfig,
    CrossWorldPeer,
    GatewayPortal,
    RealInternetConfig,
)
from netengine.spec.types import GatewayCrossWorldMode, GatewayRealInternetMode

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ─────────────────────────────────────────────
# Helpers (shared with test_e2e_fullstack)
# ─────────────────────────────────────────────


def _docker_client():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


def _cleanup_docker(client) -> None:
    for c in client.containers.list(all=True):
        if c.name.startswith(("netengine_", "netengines_")):
            try:
                c.stop(timeout=5)
                c.remove(force=True)
            except Exception:
                pass
    for n in client.networks.list():
        if n.name in ("core", "platform"):
            try:
                n.remove()
            except Exception:
                pass


def _read_corefile_from_container(client, container_name: str) -> str:
    """Return the current contents of /etc/coredns/Corefile inside the container.

    Uses Docker's get_archive API rather than exec+cat so it works with minimal
    CoreDNS images that have no shell utilities in their PATH.
    """
    container = client.containers.get(container_name)
    bits, _ = container.get_archive("/etc/coredns/Corefile")
    buf = io.BytesIO()
    for chunk in bits:
        buf.write(chunk)
    buf.seek(0)
    with tarfile.open(fileobj=buf) as tar:
        members = tar.getmembers()
        if not members:
            return ""
        f = tar.extractfile(members[0])
        return f.read().decode("utf-8", errors="replace") if f else ""


# ─────────────────────────────────────────────
# Federation test
# ─────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_cross_world_federation(tmp_path, monkeypatch):
    """PEERED mode: verify DNS stub for peer TLD is written to CoreDNS Corefile.

    The test boots phases 0-2 (substrate + CoreDNS) then runs
    GatewayPortalHandler with a PEERED cross-world spec that references a
    simulated peer world at 192.0.2.1 (TEST-NET — not routable).

    After the handler runs, the CoreDNS Corefile must contain a forwarding
    stub for `world-b.internal` pointing at the peer's DNS resolver.
    """
    monkeypatch.setenv("NETENGINE_ZONE_DIR", str(tmp_path / "coredns"))

    client = _docker_client()
    if client is None:
        pytest.skip("Docker daemon not available")

    spec = load_spec(FIXTURES_DIR / "e2e-spec.yaml")
    docker_handler = DockerHandler()
    state = RuntimeState()
    ctx = PhaseContext(
        spec=spec,
        runtime_state=state,
        logger=get_logger("e2e.federation"),
        docker_client=docker_handler,
        mock_mode=False,
        zone_dir=str(tmp_path / "coredns"),
    )

    try:
        # ── Bootstrap phases 0-2 ────────────────────────────────────────────
        await SubstrateHandler().execute(ctx)
        await DNSHandler().execute(ctx)

        coredns = client.containers.get("netengine_coredns")
        assert coredns.status == "running"

        # ── Build gateway portal spec with PEERED cross-world mode ──────────
        peer = CrossWorldPeer(
            name="world-b",
            endpoint="192.0.2.1",  # TEST-NET — safe, not routable
            mode=GatewayCrossWorldMode.PEERED,
            trust_anchor_cert=None,  # Skip trust anchor install
        )
        portal_spec = GatewayPortal(
            enabled=True,
            real_internet=RealInternetConfig(
                mode=GatewayRealInternetMode.CUSTOM  # CUSTOM is a no-op; avoids gateway container
            ),
            cross_world=CrossWorldConfig(
                mode=GatewayCrossWorldMode.PEERED,
                peers=[peer],
            ),
        )

        # Patch the spec's gateway_portal for this test
        original_portal = ctx.spec.gateway_portal
        object.__setattr__(ctx.spec, "gateway_portal", portal_spec)

        try:
            # ── Run gateway portal handler ───────────────────────────────────
            await GatewayPortalHandler().execute(ctx)
        finally:
            object.__setattr__(ctx.spec, "gateway_portal", original_portal)

        # ── Assertions ───────────────────────────────────────────────────────
        gp_output = ctx.runtime_state.gateway_portal_output
        assert gp_output is not None
        assert gp_output["enabled"] is True
        assert gp_output["cross_world_mode"] == GatewayCrossWorldMode.PEERED.value

        peers_out = gp_output.get("federation", {}).get("peers", [])
        assert len(peers_out) == 1, f"Expected 1 peer in output, got: {peers_out}"

        peer_out = peers_out[0]
        assert peer_out["name"] == "world-b"
        assert peer_out["dns_forwarding_configured"] is True, (
            "DNS stub for world-b.internal was not written to CoreDNS Corefile. "
            f"Peer output: {peer_out}"
        )

        # ── Verify Corefile content ───────────────────────────────────────────
        # Brief pause for SIGHUP to propagate
        await asyncio.sleep(1)

        corefile = _read_corefile_from_container(client, "netengine_coredns")
        assert "world-b.internal" in corefile, (
            f"Expected 'world-b.internal' stub in CoreDNS Corefile but not found.\n"
            f"Corefile:\n{corefile}"
        )
        assert (
            "192.0.2.1" in corefile
        ), f"Expected peer IP '192.0.2.1' in CoreDNS Corefile.\nCorefile:\n{corefile}"

        # CoreDNS should still be running (SIGHUP did not crash it)
        coredns.reload()
        assert (
            coredns.status == "running"
        ), f"CoreDNS crashed after Corefile update (status={coredns.status})"

    finally:
        _cleanup_docker(client)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_federation_none_mode_no_changes(tmp_path, monkeypatch):
    """NONE mode: GatewayPortalHandler skips peer setup when cross_world is NONE."""
    monkeypatch.setenv("NETENGINE_ZONE_DIR", str(tmp_path / "coredns"))

    client = _docker_client()
    if client is None:
        pytest.skip("Docker daemon not available")

    spec = load_spec(FIXTURES_DIR / "e2e-spec.yaml")
    docker_handler = DockerHandler()
    state = RuntimeState()
    ctx = PhaseContext(
        spec=spec,
        runtime_state=state,
        logger=get_logger("e2e.federation.none"),
        docker_client=docker_handler,
        mock_mode=False,
        zone_dir=str(tmp_path / "coredns"),
    )

    try:
        await SubstrateHandler().execute(ctx)
        await DNSHandler().execute(ctx)

        corefile_before = _read_corefile_from_container(client, "netengine_coredns")

        # minimal.yaml has cross_world.mode: none — run portal handler as-is
        await GatewayPortalHandler().execute(ctx)

        gp_output = ctx.runtime_state.gateway_portal_output
        assert gp_output is not None
        assert gp_output["cross_world_mode"] == GatewayCrossWorldMode.NONE.value

        corefile_after = _read_corefile_from_container(client, "netengine_coredns")
        # Corefile must not have changed — no stubs should have been added
        assert (
            corefile_before == corefile_after
        ), "Corefile was modified even though cross_world mode is NONE"

    finally:
        _cleanup_docker(client)
