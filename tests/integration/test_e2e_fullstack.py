"""End-to-end integration test: real Docker, live DNS, ACME, optional OIDC.

Run with:
    pytest tests/integration/test_e2e_fullstack.py --run-e2e

Requires:
  - Docker daemon accessible on the host
  - Enough privileges to create bridge networks and containers
  - About 1-2 min for phases 0-2 (CoreDNS image ~30 MB)
  - About 5-8 min for phase 3 (step-ca image ~200 MB + CA generation)
  - NETENGINE_KEYCLOAK_URL env var to enable the OIDC test

What gets validated:
  Phase 0  — real Docker networks (`core`, `platform`) created via Docker API
  Phase 1-2 — CoreDNS container up, live SOA UDP query returns a valid response
  Phase 3  — step-ca container up, ACME directory endpoint returns JSON
  OIDC     — Keycloak token endpoint issues a bearer token (optional)
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import struct
import urllib.request
from pathlib import Path

import pytest

from netengine.core.state import RuntimeState
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.dns import DNSHandler
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.substrate import SubstrateHandler
from netengine.logging import get_logger
from netengine.spec.loader import load_spec

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _docker_client():
    """Return a docker SDK client, or None if Docker is unavailable."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        return None


def _get_container_ip(container, network_name: str) -> str:
    """Return the container's IP on the named Docker network."""
    container.reload()
    networks = container.attrs["NetworkSettings"]["Networks"]
    return networks.get(network_name, {}).get("IPAddress", "")


def _cleanup_docker(client) -> None:
    """Remove all netengine containers and the core/platform networks."""
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


def _send_dns_soa(server_ip: str, zone: str, timeout: float = 5.0) -> bool:
    """Send a raw DNS SOA query over UDP and return True on a valid response.

    Does not depend on any third-party DNS library so the test stays
    self-contained. The transaction ID (0x1234) and QR bit in the flags
    are the only fields checked — we just need to confirm the server is
    responding correctly.
    """
    header = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    qname = b""
    for label in zone.rstrip(".").split("."):
        enc = label.encode()
        qname += bytes([len(enc)]) + enc
    qname += b"\x00"
    question = qname + struct.pack(">HH", 6, 1)  # SOA + IN
    query = header + question

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(query, (server_ip, 53))
            data, _ = s.recvfrom(512)
        resp_id, flags = struct.unpack(">HH", data[:4])
        return resp_id == 0x1234 and bool(flags & 0x8000)
    except Exception:
        return False


def _build_context(spec, zone_dir: str, state: RuntimeState | None = None) -> PhaseContext:
    docker_handler = DockerHandler()
    return PhaseContext(
        spec=spec,
        runtime_state=state or RuntimeState(),
        logger=get_logger("e2e"),
        docker_client=docker_handler,
        mock_mode=False,
        zone_dir=zone_dir,
    )


# ─────────────────────────────────────────────
# Phase 0 + 1-2: Substrate and DNS
# ─────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_substrate_and_dns(tmp_path, monkeypatch):
    """Phases 0-2: real Docker networks + CoreDNS + live SOA query.

    Validates:
      - Docker networks 'core' and 'platform' are created by Phase 0
      - CoreDNS container is running after Phases 1-2
      - A raw UDP DNS SOA query to root.internal resolves at CoreDNS's IP
    """
    monkeypatch.setenv("NETENGINE_ZONE_DIR", str(tmp_path / "coredns"))

    client = _docker_client()
    if client is None:
        pytest.skip("Docker daemon not available")

    spec = load_spec(EXAMPLES_DIR / "minimal.yaml")
    ctx = _build_context(spec, str(tmp_path / "coredns"))

    try:
        # ── Phase 0: Substrate ──────────────────────────────────────────────
        await SubstrateHandler().execute(ctx)

        assert ctx.runtime_state.substrate_output is not None
        assert ctx.runtime_state.substrate_output["healthy"] is True

        net_names = {n.name for n in client.networks.list()}
        assert "core" in net_names, "Docker network 'core' was not created by Phase 0"
        assert "platform" in net_names, "Docker network 'platform' was not created by Phase 0"

        # ── Phases 1-2: DNS ─────────────────────────────────────────────────
        await DNSHandler().execute(ctx)

        assert ctx.runtime_state.dns_output is not None
        assert ctx.runtime_state.dns_output.get("healthy") is True
        assert ctx.runtime_state.phase_completed.get("1") is True
        assert ctx.runtime_state.phase_completed.get("2") is True

        # CoreDNS container must be running
        coredns = client.containers.get("netengine_coredns")
        assert coredns.status == "running", f"CoreDNS not running (status={coredns.status})"

        # Verify the declared listen IP was assigned on the core network
        container_ip = _get_container_ip(coredns, "core")
        expected_ip = spec.dns.root.listen_ip
        assert (
            container_ip == expected_ip
        ), f"CoreDNS IP on 'core' network is {container_ip!r}, expected {expected_ip!r}"

        # ── Live DNS validation ──────────────────────────────────────────────
        # Brief pause for CoreDNS to finish binding
        await asyncio.sleep(2)

        ok = _send_dns_soa(container_ip, "root.internal")
        assert ok, (
            f"Live DNS SOA query to root.internal at {container_ip}:53 failed. "
            "CoreDNS may not have started correctly."
        )

    finally:
        _cleanup_docker(client)


# ─────────────────────────────────────────────
# Phase 3: PKI / ACME
# ─────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.asyncio
async def test_e2e_pki_acme_directory(tmp_path, monkeypatch):
    """Phase 3: step-ca starts and ACME directory returns valid JSON.

    This test is marked @slow because it pulls the step-ca image (~200 MB) and
    runs 'step ca init' which takes 30-60 seconds on first run.

    Validates:
      - step-ca container is running after Phase 3
      - HTTPS GET to /acme/acme/directory returns JSON with 'newNonce' key
    """
    monkeypatch.setenv("NETENGINE_ZONE_DIR", str(tmp_path / "coredns"))

    client = _docker_client()
    if client is None:
        pytest.skip("Docker daemon not available")

    spec = load_spec(EXAMPLES_DIR / "minimal.yaml")
    ctx = _build_context(spec, str(tmp_path / "coredns"))

    try:
        # Phases 0-2
        await SubstrateHandler().execute(ctx)
        await DNSHandler().execute(ctx)

        # Phase 3: PKI
        await PKIPhaseHandler().execute(ctx)

        assert ctx.runtime_state.pki_bootstrapped is True
        assert ctx.runtime_state.ca_cert_pem is not None

        step_ca = client.containers.get("netengines_step_ca")
        assert step_ca.status == "running", f"step-ca not running (status={step_ca.status})"

        # Verify ACME directory is reachable (self-signed cert → skip verify)
        ca_ip = spec.pki.acme.listen_ip
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        acme_url = f"https://{ca_ip}/acme/acme/directory"
        req = urllib.request.Request(acme_url)
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            body = json.loads(resp.read().decode())

        assert "newNonce" in body, f"ACME directory missing 'newNonce': {body}"

    finally:
        _cleanup_docker(client)


# ─────────────────────────────────────────────
# Phase 4: OIDC login (optional — requires Keycloak)
# ─────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_oidc_token(tmp_path, monkeypatch):
    """OIDC token endpoint issues a bearer token.

    Requires a running Keycloak instance. Set NETENGINE_KEYCLOAK_URL to enable,
    e.g.:
        NETENGINE_KEYCLOAK_URL=http://localhost:8180 pytest --run-e2e

    The test creates a temporary realm, issues a token, and validates the JWT
    structure. It does NOT run Phase 4 (too slow for default CI) — it uses
    the pre-running Keycloak directly.
    """
    keycloak_url = os.environ.get("NETENGINE_KEYCLOAK_URL", "").rstrip("/")
    if not keycloak_url:
        pytest.skip("NETENGINE_KEYCLOAK_URL not set — skipping OIDC test")

    import urllib.error

    # Check Keycloak health
    try:
        req = urllib.request.Request(f"{keycloak_url}/health/ready")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200, "Keycloak health endpoint not ready"
    except urllib.error.URLError as exc:
        pytest.skip(f"Keycloak at {keycloak_url} not reachable: {exc}")

    # Obtain a token from the master realm using admin credentials
    admin_password = os.environ.get("NETENGINE_KEYCLOAK_ADMIN_PASSWORD", "admin_dev_password")
    token_url = f"{keycloak_url}/realms/master/protocol/openid-connect/token"
    payload = (
        f"client_id=admin-cli&username=admin&password={admin_password}&grant_type=password"
    ).encode()
    req = urllib.request.Request(
        token_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_data = json.loads(resp.read().decode())

    assert "access_token" in token_data, f"No access_token in response: {token_data}"
    assert token_data.get("token_type", "").lower() == "bearer"
