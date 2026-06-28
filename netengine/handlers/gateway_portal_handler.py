"""Gateway Portal handler — Real Internet access and Cross-World Federation.

This is a boundary handler, not a numbered phase. It is invoked after Phase 7
(ANDs) and applies two independent policies declared in the spec:

  * ``gateway_portal.real_internet`` — controls how the world connects to
    the public internet (ISOLATED / SHADOWED / MIRRORED / EXPOSED / CUSTOM).

  * ``gateway_portal.cross_world`` — controls peering and federation with
    other NetEngine worlds (NONE / PEERED / FEDERATED).
"""

from datetime import datetime
from typing import Any

from netengine.errors import GatewayError, PKIError
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.gateway_handler import GatewayHandler
from netengine.logging import get_logger
from netengine.spec.models import CrossWorldPeer, GatewayPortal
from netengine.spec.types import GatewayCrossWorldMode, GatewayRealInternetMode

logger = get_logger(__name__)


class GatewayPortalHandler(BasePhaseHandler):
    """Apply real-internet and cross-world policies after ANDs are provisioned."""

    async def execute(self, context: PhaseContext) -> None:
        spec = context.spec
        portal: GatewayPortal = spec.gateway_portal

        if not portal.enabled:
            context.logger.info("Gateway portal disabled — skipping")
            context.runtime_state.gateway_portal_output = {
                "enabled": False,
                "deployed_at": datetime.utcnow().isoformat(),
            }
            context.runtime_state.save()
            return

        context.logger.info("Applying gateway portal policies")

        if context.mock_mode:
            context.runtime_state.gateway_portal_output = {
                "enabled": True,
                "internet_mode": portal.real_internet.mode.value,
                "cross_world_mode": portal.cross_world.mode.value,
                "peer_count": len(portal.cross_world.peers),
                "mock": True,
                "deployed_at": datetime.utcnow().isoformat(),
            }
            context.runtime_state.save()
            await self._emit_event(context, "gateway_portal.ready", context.runtime_state.gateway_portal_output)
            return

        docker = context.docker_client if context.docker_client is not None else DockerHandler()
        gateway = GatewayHandler(docker)

        # ── Real Internet ───────────────────────────────────────────────────
        internet_output = await self._apply_internet_policy(context, gateway, portal)

        # ── Cross-World Federation ──────────────────────────────────────────
        federation_output = await self._apply_cross_world(context, gateway, docker, portal)

        # ── Persist ────────────────────────────────────────────────────────
        context.runtime_state.gateway_portal_output = {
            "enabled": True,
            "internet_mode": portal.real_internet.mode.value,
            "cross_world_mode": portal.cross_world.mode.value,
            "peer_count": len(portal.cross_world.peers),
            "internet": internet_output,
            "federation": federation_output,
            "deployed_at": datetime.utcnow().isoformat(),
        }
        context.runtime_state.save()

        await self._emit_event(
            context,
            "gateway_portal.ready",
            context.runtime_state.gateway_portal_output,
        )
        context.logger.info("Gateway portal policies applied")

    async def healthcheck(self, context: PhaseContext) -> bool:
        return context.runtime_state.gateway_portal_output is not None

    async def should_skip(self, context: PhaseContext) -> bool:
        return context.runtime_state.gateway_portal_output is not None

    # ─────────────────────────────────────────────
    # Internet policy
    # ─────────────────────────────────────────────

    async def _apply_internet_policy(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        portal: GatewayPortal,
    ) -> dict:
        config = portal.real_internet
        context.logger.info(f"Real-internet mode: {config.mode.value}")

        await gateway.apply_internet_policy(config)

        output: dict[str, Any] = {"mode": config.mode.value}

        if config.upstream_resolver_enabled and config.upstream_resolver_ip:
            await self._configure_upstream_resolver(
                context, config.upstream_resolver_ip
            )
            output["upstream_resolver"] = config.upstream_resolver_ip

        if config.mode == GatewayRealInternetMode.MIRRORED and config.service_mirrors:
            output["mirrors"] = [
                {"real": m.real_hostname, "in_world": m.in_world_service}
                for m in config.service_mirrors
            ]

        return output

    async def _configure_upstream_resolver(
        self, context: PhaseContext, resolver_ip: str
    ) -> None:
        """Inject an upstream forwarder into the CoreDNS root Corefile.

        Adds a ``forward . <resolver_ip>`` directive so that names not
        resolved within the world are forwarded to the real internet resolver.
        This method is best-effort: a failure is logged but does not abort
        the portal setup.
        """
        if context.docker_client is None:
            return

        try:
            corefile_patch = (
                f"\n# Upstream internet resolver (gateway portal)\n"
                f"forward . {resolver_ip}\n"
            )
            corefile_append_cmd = [
                "sh", "-c",
                f"echo '{corefile_patch}' >> /etc/coredns/Corefile",
            ]
            exit_code, output = await context.docker_client.exec_command(
                "netengine_coredns", corefile_append_cmd
            )
            if exit_code != 0:
                context.logger.warning(
                    f"Could not append upstream resolver to CoreDNS: {output}"
                )
            else:
                # Reload CoreDNS to pick up the change
                await context.docker_client.exec_command(
                    "netengine_coredns", ["kill", "-HUP", "1"]
                )
                context.logger.info(f"Upstream resolver configured: {resolver_ip}")
        except Exception as exc:
            context.logger.warning(f"Upstream resolver setup skipped: {exc}")

    # ─────────────────────────────────────────────
    # Cross-world federation
    # ─────────────────────────────────────────────

    async def _apply_cross_world(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        docker: DockerHandler,
        portal: GatewayPortal,
    ) -> dict:
        cross_world = portal.cross_world

        if cross_world.mode == GatewayCrossWorldMode.NONE:
            context.logger.info("Cross-world mode: NONE — no peering configured")
            return {"mode": GatewayCrossWorldMode.NONE.value, "peers": []}

        context.logger.info(
            f"Cross-world mode: {cross_world.mode.value} "
            f"({len(cross_world.peers)} peer(s))"
        )

        peers_output = []
        for peer in cross_world.peers:
            peer_result = await self._setup_peer(context, gateway, docker, peer)
            peers_output.append(peer_result)

        return {
            "mode": cross_world.mode.value,
            "peers": peers_output,
        }

    async def _setup_peer(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        docker: DockerHandler,
        peer: CrossWorldPeer,
    ) -> dict:
        """Wire up a single cross-world peer: trust anchor + routing + DNS."""
        context.logger.info(f"Setting up cross-world peer: {peer.name} ({peer.endpoint})")
        result: dict[str, Any] = {
            "name": peer.name,
            "endpoint": peer.endpoint,
            "mode": peer.mode.value,
        }

        # 1. Install trust anchor certificate
        if peer.trust_anchor_cert:
            try:
                await self._install_trust_anchor(context, docker, peer.name, peer.trust_anchor_cert)
                result["trust_anchor_installed"] = True
            except Exception as exc:
                context.logger.warning(
                    f"Trust anchor install failed for peer {peer.name}: {exc}"
                )
                result["trust_anchor_installed"] = False
                result["trust_anchor_error"] = str(exc)
        else:
            result["trust_anchor_installed"] = False

        # 2. Configure nftables routing to peer endpoint
        try:
            # Extract host from endpoint (strip port if present)
            peer_ip = peer.endpoint.split(":")[0]
            await gateway.apply_peer_routing(peer.name, peer_ip)
            result["routing_configured"] = True
        except GatewayError as exc:
            context.logger.warning(f"Peer routing failed for {peer.name}: {exc}")
            result["routing_configured"] = False
            result["routing_error"] = str(exc)

        # 3. Configure DNS forwarding for peer domains
        try:
            await self._configure_peer_dns(context, peer)
            result["dns_forwarding_configured"] = True
        except Exception as exc:
            context.logger.warning(f"Peer DNS forwarding failed for {peer.name}: {exc}")
            result["dns_forwarding_configured"] = False
            result["dns_error"] = str(exc)

        return result

    async def _install_trust_anchor(
        self,
        context: PhaseContext,
        docker: DockerHandler,
        peer_name: str,
        cert_pem: str,
    ) -> None:
        """Install a peer's CA certificate into the gateway container trust store.

        Writes the PEM cert to ``/usr/local/share/ca-certificates/<peer>.crt``
        then runs ``update-ca-certificates`` so that TLS connections to the peer
        are automatically trusted by any process running in the gateway.
        """
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as f:
            f.write(cert_pem)
            tmp_path = f.name
        try:
            dest_path = f"/usr/local/share/ca-certificates/peer_{peer_name}.crt"
            await docker.copy_to_container("netengine_gateway", tmp_path, dest_path)
        finally:
            os.unlink(tmp_path)

        exit_code, output = await docker.exec_command(
            "netengine_gateway", ["update-ca-certificates"]
        )
        if exit_code != 0:
            raise PKIError(
                f"update-ca-certificates failed for peer {peer_name}: {output}"
            )

        context.logger.info(f"Trust anchor installed for peer: {peer_name}")

    async def _configure_peer_dns(
        self, context: PhaseContext, peer: CrossWorldPeer
    ) -> None:
        """Add a CoreDNS forwarding zone for the peer world's TLD.

        Derives the peer TLD from ``<peer.name>.internal`` and adds a
        ``forward <tld> <peer_resolver>`` stub to the CoreDNS root Corefile.
        The peer's DNS resolver is assumed to live at port 53 of the peer endpoint.
        """
        if context.docker_client is None:
            return

        peer_tld = f"{peer.name}.internal"
        peer_ip = peer.endpoint.split(":")[0]

        corefile_stub = (
            f"\n# Cross-world peer: {peer.name}\n"
            f"{peer_tld} {{\n"
            f"    forward . {peer_ip}:53\n"
            f"}}\n"
        )
        exit_code, output = await context.docker_client.exec_command(
            "netengine_coredns",
            ["sh", "-c", f"echo '{corefile_stub}' >> /etc/coredns/Corefile"],
        )
        if exit_code != 0:
            raise GatewayError(
                f"Could not configure DNS forwarding for peer {peer.name}: {output}"
            )

        # Signal CoreDNS to reload config
        await context.docker_client.exec_command("netengine_coredns", ["kill", "-HUP", "1"])
        context.logger.info(f"DNS forwarding configured for peer TLD: {peer_tld}")

    # ─────────────────────────────────────────────
    # Event emission
    # ─────────────────────────────────────────────

    async def _emit_event(self, context: PhaseContext, event_type: str, payload: dict) -> None:
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="gateway_portal_handler",
            payload=payload,
            correlation_id=getattr(context.runtime_state, "correlation_id", None),
            parent_event_id=getattr(context.runtime_state, "parent_event_id", None),
        )
        context.logger.info(f"Event emitted: {event_type}")
        if context.pgmq_client is not None:
            try:
                await context.pgmq_client.send(event)
            except Exception as exc:
                context.logger.warning(f"Failed to queue gateway portal event: {exc}")
