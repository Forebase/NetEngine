"""Gateway Portal handler — Real Internet access and Cross-World Federation.

This is a boundary handler, not a numbered phase. It is invoked after Phase 7
(ANDs) and applies two independent policies declared in the spec:

  * ``gateway_portal.real_internet`` — controls how the world connects to
    the public internet (ISOLATED / SHADOWED / MIRRORED / EXPOSED / CUSTOM).

  * ``gateway_portal.cross_world`` — controls peering and federation with
    other NetEngine worlds (NONE / PEERED / FEDERATED).
"""

import os
import re
from datetime import datetime
from typing import Any

from netengine.errors import GatewayError, PKIError
from netengine.events.emitter import emit_event
from netengine.events.queues import Queue
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.gateway_handler import GatewayHandler
from netengine.handlers.protocols import DockerAdapterProtocol
from netengine.logs import get_logger
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
            await self._emit_event(
                context,
                "gateway_portal.ready",
                context.runtime_state.gateway_portal_output,
            )
            return

        if context.docker_client is None:
            raise RuntimeError(
                "Gateway portal requires context.docker_client when mock_mode is disabled"
            )
        docker = context.docker_client
        gateway = GatewayHandler(docker)

        # ── Real Internet ───────────────────────────────────────────────────
        internet_output = await self._apply_internet_policy(context, gateway, portal)

        # ── Cross-World Federation ──────────────────────────────────────────
        federation_output = await self._apply_cross_world(
            context, gateway, docker, portal
        )

        # ── Persist ────────────────────────────────────────────────────────
        context.runtime_state.gateway_portal_output = {
            "enabled": True,
            "internet_mode": portal.real_internet.mode.value,
            "cross_world_mode": portal.cross_world.mode.value,
            "peer_count": len(portal.cross_world.peers),
            "internet": internet_output,
            "federation": federation_output,
            "peer_artifacts": federation_output.get("peers", []),
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
        output = context.runtime_state.gateway_portal_output
        if output is None:
            return False
        if not output.get("enabled", True) or output.get("mock"):
            return True
        if context.docker_client is None:
            return False

        gateway = GatewayHandler(context.docker_client)
        checks: dict[str, Any] = {
            "gateway_reachable": await gateway.gateway_reachable()
        }
        if not checks["gateway_reachable"]:
            output["healthchecks"] = checks
            return False

        internet_mode = output.get("internet_mode")
        if internet_mode and internet_mode != GatewayRealInternetMode.CUSTOM.value:
            checks["internet_nft_table"] = await gateway.nft_table_exists(
                "inet", "netengine_internet"
            )

        peers = output.get("peer_artifacts", [])
        peer_checks = []
        for peer in peers:
            peer_check = {"name": peer.get("name")}
            peer_check["nft_table"] = await gateway.nft_table_exists(
                "inet", peer.get("nft_table", f"netengine_peer_{peer.get('name')}")
            )
            trust_anchor_path = peer.get("trust_anchor_path")
            peer_check["trust_anchor"] = (
                True
                if not trust_anchor_path
                else await gateway.path_exists(trust_anchor_path)
            )
            peer_check["dns_stub"] = self._corefile_contains_stub(context, peer)
            endpoint_status = await gateway.peer_endpoint_reachable(
                peer.get("endpoint", "")
            )
            if endpoint_status is not None:
                peer_check["endpoint_reachable"] = endpoint_status
            peer_checks.append(peer_check)
        checks["peers"] = peer_checks
        output["healthchecks"] = checks
        return all(v is True for k, v in checks.items() if k != "peers") and all(
            all(value is True for key, value in peer.items() if key != "name")
            for peer in peer_checks
        )

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
    ) -> dict[str, Any]:
        config = portal.real_internet
        context.logger.info(f"Real-internet mode: {config.mode.value}")

        try:
            await gateway.apply_internet_policy(config)
        except GatewayError as exc:
            context.logger.warning(
                f"Internet policy ({config.mode.value}) not applied — "
                f"gateway container unavailable: {exc}"
            )

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
        Writes to the host-mounted Corefile to avoid shell dependency in the
        CoreDNS container. This method is best-effort: failures are logged
        but do not abort portal setup.
        """
        if context.docker_client is None:
            return

        try:
            corefile_patch = (
                f"\n# Upstream internet resolver (gateway portal)\n"
                f"forward . {resolver_ip}\n"
            )
            corefile_path = os.path.join(context.zone_dir, "Corefile")
            with open(corefile_path, "a") as f:
                f.write(corefile_patch)
            await context.docker_client.signal_container("netengine_coredns", "HUP")
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
        docker: DockerAdapterProtocol,
        portal: GatewayPortal,
    ) -> dict[str, Any]:
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
            peer_result = await self.add_peer(context, gateway, docker, peer)
            peers_output.append(peer_result)

        return {
            "mode": cross_world.mode.value,
            "peers": peers_output,
        }

    async def add_peer(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        docker: DockerAdapterProtocol,
        peer: CrossWorldPeer,
    ) -> dict[str, Any]:
        """Explicit lifecycle operation to add a peer with rollback on failure."""
        return await self._setup_peer(
            context, gateway, docker, peer, rollback_on_failure=True
        )

    async def update_peer(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        docker: DockerAdapterProtocol,
        peer: CrossWorldPeer,
    ) -> dict[str, Any]:
        """Replace a peer's installed artifacts with the supplied definition."""
        await self.remove_peer(context, gateway, peer.name)
        return await self.add_peer(context, gateway, docker, peer)

    async def remove_peer(
        self, context: PhaseContext, gateway: GatewayHandler, peer_name: str
    ) -> None:
        """Explicit lifecycle operation to remove routing, trust, and DNS artifacts."""
        await gateway.remove_peer_routing(peer_name)
        if context.docker_client is not None:
            await context.docker_client.exec_command(
                "netengine_gateway",
                ["rm", "-f", self._trust_anchor_path(peer_name)],
            )
            await context.docker_client.exec_command(
                "netengine_gateway", ["update-ca-certificates"]
            )
        self._remove_peer_dns(context, peer_name)
        self._drop_peer_artifact(context, peer_name)

    async def rotate_trust_anchor(
        self,
        context: PhaseContext,
        docker: DockerAdapterProtocol,
        peer_name: str,
        cert_pem: str,
    ) -> dict[str, Any]:
        """Explicit lifecycle operation to rotate an installed peer trust anchor."""
        await self._install_trust_anchor(context, docker, peer_name, cert_pem)
        artifact = self._upsert_peer_artifact(context, {"name": peer_name})
        artifact["trust_anchor_path"] = self._trust_anchor_path(peer_name)
        artifact["trust_anchor_installed_at"] = datetime.utcnow().isoformat()
        context.runtime_state.save()
        return artifact

    async def reapply_routing_after_gateway_restart(
        self, context: PhaseContext, gateway: GatewayHandler
    ) -> None:
        """Reload persisted peer routing rule files after gateway restart."""
        output = context.runtime_state.gateway_portal_output or {}
        await gateway.reapply_peer_routing(output.get("peer_artifacts", []))

    async def _setup_peer(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        docker: DockerAdapterProtocol,
        peer: CrossWorldPeer,
        rollback_on_failure: bool = False,
    ) -> dict[str, Any]:
        """Wire up a single cross-world peer: trust anchor + routing + DNS."""
        context.logger.info(
            f"Setting up cross-world peer: {peer.name} ({peer.endpoint})"
        )
        peer_ip = peer.endpoint.split(":")[0]
        artifact = {
            "name": peer.name,
            "endpoint": peer.endpoint,
            "mode": peer.mode.value,
            "nft_table": f"netengine_peer_{self._safe_peer_name(peer.name)}",
            "routing_rules_path": f"/etc/nftables/rules/peer_{self._safe_peer_name(peer.name)}.nft",
            "dns_zone": f"{peer.name}.internal",
            "dns_forwarder": f"{peer_ip}:53",
            "trust_anchor_path": self._trust_anchor_path(peer.name)
            if peer.trust_anchor_cert
            else None,
            "installed_at": datetime.utcnow().isoformat(),
            "trust_anchor_installed": False,
            "routing_configured": False,
            "dns_forwarding_configured": False,
        }
        completed: list[str] = []
        try:
            if peer.trust_anchor_cert:
                await self._install_trust_anchor(
                    context, docker, peer.name, peer.trust_anchor_cert
                )
                completed.append("trust_anchor")
                artifact["trust_anchor_installed"] = True
            else:
                artifact["trust_anchor_installed"] = False

            await gateway.apply_peer_routing(peer.name, peer_ip)
            completed.append("routing")
            artifact["routing_configured"] = True

            await self._configure_peer_dns(context, peer)
            completed.append("dns")
            artifact["dns_forwarding_configured"] = True
            self._upsert_peer_artifact(context, artifact)
            return artifact
        except Exception as exc:
            context.logger.warning(f"Peer setup failed for {peer.name}: {exc}")
            if rollback_on_failure:
                await self._rollback_peer_setup(
                    context, gateway, docker, peer.name, completed
                )
            artifact["error"] = str(exc)
            if "routing" not in completed:
                artifact["routing_error"] = str(exc)
            elif "dns" not in completed:
                artifact["dns_error"] = str(exc)
            artifact["rolled_back"] = rollback_on_failure
            return artifact

    async def _install_trust_anchor(
        self,
        context: PhaseContext,
        docker: DockerAdapterProtocol,
        peer_name: str,
        cert_pem: str,
    ) -> None:
        """Install a peer's CA certificate into the gateway container trust store.

        Writes the PEM cert to ``/usr/local/share/ca-certificates/<peer>.crt``
        then runs ``update-ca-certificates`` so that TLS connections to the peer
        are automatically trusted by any process running in the gateway.
        """
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as f:
            f.write(cert_pem)
            tmp_path = f.name
        try:
            dest_path = self._trust_anchor_path(peer_name)
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
        Writes to the host-mounted Corefile to avoid shell dependency in the
        CoreDNS container (which may not have sh in its PATH).
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

        corefile_path = os.path.join(context.zone_dir, "Corefile")
        try:
            with open(corefile_path, "a") as f:
                f.write(corefile_stub)
        except OSError as exc:
            raise GatewayError(
                f"Could not append to Corefile at {corefile_path}: {exc}"
            ) from exc

        # Signal CoreDNS to reload config via Docker daemon (no shell required)
        await context.docker_client.signal_container("netengine_coredns", "HUP")
        context.logger.info(f"DNS forwarding configured for peer TLD: {peer_tld}")

    def _safe_peer_name(self, peer_name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", peer_name)

    def _trust_anchor_path(self, peer_name: str) -> str:
        return f"/usr/local/share/ca-certificates/peer_{self._safe_peer_name(peer_name)}.crt"

    def _upsert_peer_artifact(
        self, context: PhaseContext, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        output = context.runtime_state.gateway_portal_output or {}
        artifacts = list(output.get("peer_artifacts", []))
        artifacts = [p for p in artifacts if p.get("name") != artifact.get("name")]
        artifacts.append(artifact)
        output["peer_artifacts"] = artifacts
        context.runtime_state.gateway_portal_output = output
        context.runtime_state.save()
        return artifact

    def _drop_peer_artifact(self, context: PhaseContext, peer_name: str) -> None:
        output = context.runtime_state.gateway_portal_output or {}
        output["peer_artifacts"] = [
            p for p in output.get("peer_artifacts", []) if p.get("name") != peer_name
        ]
        context.runtime_state.gateway_portal_output = output
        context.runtime_state.save()

    async def _rollback_peer_setup(
        self,
        context: PhaseContext,
        gateway: GatewayHandler,
        docker: DockerAdapterProtocol,
        peer_name: str,
        completed: list[str],
    ) -> None:
        if "dns" in completed:
            self._remove_peer_dns(context, peer_name)
        if "routing" in completed:
            try:
                await gateway.remove_peer_routing(peer_name)
            except Exception as exc:
                context.logger.warning(
                    f"Peer routing rollback failed for {peer_name}: {exc}"
                )
        if "trust_anchor" in completed:
            await docker.exec_command(
                "netengine_gateway", ["rm", "-f", self._trust_anchor_path(peer_name)]
            )
            await docker.exec_command("netengine_gateway", ["update-ca-certificates"])
        self._drop_peer_artifact(context, peer_name)

    def _remove_peer_dns(self, context: PhaseContext, peer_name: str) -> None:
        corefile_path = os.path.join(context.zone_dir, "Corefile")
        try:
            text = open(corefile_path).read()
        except OSError:
            return
        pattern = re.compile(
            rf"\n?# Cross-world peer: {re.escape(peer_name)}\n{re.escape(peer_name)}\.internal \{{\n    forward \. [^\n]+\n\}}\n?",
            re.MULTILINE,
        )
        new_text = pattern.sub("\n", text)
        if new_text != text:
            with open(corefile_path, "w") as f:
                f.write(new_text)
        if context.docker_client is not None:
            # Fire-and-forget is not possible in this sync helper; callers also reload on add.
            pass

    def _corefile_contains_stub(
        self, context: PhaseContext, peer: dict[str, Any]
    ) -> bool:
        try:
            text = open(os.path.join(context.zone_dir, "Corefile")).read()
        except OSError:
            return False
        return (
            peer.get("dns_zone", "") in text and peer.get("dns_forwarder", "") in text
        )

    # ─────────────────────────────────────────────
    # Event emission
    # ─────────────────────────────────────────────

    async def _emit_event(
        self, context: PhaseContext, event_type: str, payload: dict[str, Any]
    ) -> None:
        await emit_event(
            context,
            event_type=event_type,
            emitted_by="gateway_portal_handler",
            payload=payload,
            queue=Queue.GATEWAY_PORTAL_EVENTS,
        )
