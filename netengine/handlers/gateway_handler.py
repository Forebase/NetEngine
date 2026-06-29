import ipaddress
import os
import tempfile
from typing import TYPE_CHECKING, Any

from netengine.errors import GatewayError
from netengine.gateways.base import BaseGatewayHandler

if TYPE_CHECKING:
    from netengine.spec.models import RealInternetConfig


class GatewayHandler(BaseGatewayHandler):
    def __init__(self, docker: Any) -> None:
        self.docker = docker
        self.gateway_container = "netengine_gateway"

    async def gateway_reachable(self) -> bool:
        """Return True when the gateway container accepts exec commands."""
        try:
            exit_code, _ = await self.docker.exec_command(
                self.gateway_container, ["true"]
            )
            return exit_code == 0
        except Exception:
            return False

    async def nft_table_exists(self, family: str, table_name: str) -> bool:
        """Return True when an nftables table exists in the gateway container."""
        exit_code, _ = await self.docker.exec_command(
            self.gateway_container, ["nft", "list", "table", family, table_name]
        )
        return exit_code == 0

    async def path_exists(self, path: str) -> bool:
        """Return True when *path* exists in the gateway container."""
        exit_code, _ = await self.docker.exec_command(
            self.gateway_container, ["test", "-e", path]
        )
        return exit_code == 0

    async def peer_endpoint_reachable(
        self, endpoint: str, timeout_seconds: int = 2
    ) -> bool | None:
        """Best-effort TCP reachability probe for host:port endpoints.

        Returns None when the endpoint has no explicit port or the container lacks
        a probe utility; callers can treat that as unknown rather than unhealthy.
        """
        host, sep, port = endpoint.rpartition(":")
        if not sep or not host or not port.isdigit():
            return None
        probes = (
            ["nc", "-z", "-w", str(timeout_seconds), host, port],
            [
                "sh",
                "-c",
                f"timeout {timeout_seconds} bash -c '</dev/tcp/{host}/{port}'",
            ],
        )
        unsupported = False
        for cmd in probes:
            exit_code, output = await self.docker.exec_command(
                self.gateway_container, cmd
            )
            if exit_code == 0:
                return True
            unsupported = (
                unsupported
                or "not found" in output.lower()
                or "no such file" in output.lower()
            )
        return None if unsupported else False

    async def reapply_peer_routing(self, peers: list[dict[str, Any]]) -> None:
        """Re-load persisted peer nftables rule files after a gateway restart."""
        for peer in peers:
            peer_name = peer.get("name")
            rules_path = (
                peer.get("routing_rules_path")
                or f"/etc/nftables/rules/peer_{peer_name}.nft"
            )
            exit_code, output = await self.docker.exec_command(
                self.gateway_container, ["nft", "-f", rules_path]
            )
            if exit_code != 0:
                raise GatewayError(
                    f"Failed to reapply peer routing for {peer_name}: {output}"
                )

    async def generate_rules(self, and_name: str, profile: str, cidr: str) -> str:
        """Generate nftables ruleset for the given AND profile."""
        if profile == "residential":
            return self._residential_rules(and_name, cidr)
        elif profile == "business":
            return self._business_rules(and_name, cidr)
        elif profile == "datacenter":
            return self._datacenter_rules(and_name, cidr)
        elif profile == "airgapped":
            return self._airgapped_rules(and_name, cidr)
        else:
            raise GatewayError(f"Unknown AND profile: {profile}")

    def _residential_rules(self, and_name: str, cidr: str) -> str:
        return f"""
table ip netengine_{and_name} {{
    chain forward {{
        type filter hook forward priority 0; policy drop;
        iifname "eth_core" oifname "eth_{and_name}" ct state established,related accept
        iifname "eth_{and_name}" oifname "eth_core" accept
        iifname "eth_{and_name}" oifname "eth_{and_name}" drop
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
        oifname "eth_core" masquerade
    }}
    chain prerouting {{
        type nat hook prerouting priority -100; policy drop;
        iifname "eth_core" ct state new drop
    }}
}}
"""

    def _business_rules(self, and_name: str, cidr: str) -> str:
        return f"""
table ip netengine_{and_name} {{
    chain forward {{
        type filter hook forward priority 0; policy drop;
        iifname "eth_core" oifname "eth_{and_name}" ct state established,related accept
        iifname "eth_{and_name}" oifname "eth_core" accept
        iifname "eth_{and_name}" oifname "eth_core" ct state new accept
        iifname "eth_{and_name}" oifname "eth_{and_name}" drop
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
    }}
}}
"""

    def _datacenter_rules(self, and_name: str, cidr: str) -> str:
        return f"""
table ip netengine_{and_name} {{
    chain forward {{
        type filter hook forward priority 0; policy accept;
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
    }}
}}
"""

    def _airgapped_rules(self, and_name: str, cidr: str) -> str:
        return f"""
table ip netengine_{and_name} {{
    chain forward {{
        type filter hook forward priority 0; policy drop;
    }}
}}
"""

    async def apply_rules(self, and_name: str, rules: str) -> None:
        """Write rules to gateway container via a temp file and reload nftables."""
        dest_path = f"/etc/nftables/rules/{and_name}.nft"

        # Write to a local temp file then copy into the container — avoids shell
        # injection and handles multi-line rulesets correctly.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".nft", delete=False) as f:
            f.write(rules)
            tmp_path = f.name
        try:
            await self.docker.copy_to_container(
                self.gateway_container, tmp_path, dest_path
            )
        finally:
            os.unlink(tmp_path)

        cmd = ["nft", "-f", dest_path]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0:
            raise GatewayError(
                f"Failed to apply nftables rules for {and_name}: {output}"
            )

    async def remove_rules(self, and_name: str) -> None:
        """Delete the nftables table for this AND."""
        cmd = ["nft", "delete", "table", "ip", f"netengine_{and_name}"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        # Table-not-found is acceptable on teardown
        if exit_code != 0 and "No such table" not in output:
            raise GatewayError(
                f"Failed to remove nftables table for {and_name}: {output}"
            )
        cmd = ["rm", "-f", f"/etc/nftables/rules/{and_name}.nft"]
        await self.docker.exec_command(self.gateway_container, cmd)

    async def reload(self) -> None:
        """Reload all nftables rules on the gateway container."""
        cmd = ["nft", "-f", "/etc/nftables/rules/main.nft"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0:
            raise GatewayError(f"Gateway nftables reload failed: {output}")

    # ─────────────────────────────────────────────
    # Real Internet Gateway Policy
    # ─────────────────────────────────────────────

    async def apply_internet_policy(self, config: "RealInternetConfig") -> None:
        """Apply real internet access policy rules to the gateway container.

        Generates and loads an nftables ruleset that enforces the mode declared
        in *config*.  CUSTOM mode is a no-op (operator manages rules directly).
        Raises GatewayError if the gateway container does not exist or the
        nftables command fails.
        """
        from netengine.spec.types import GatewayRealInternetMode

        if config.mode == GatewayRealInternetMode.CUSTOM:
            return

        rules = self._internet_rules(config)
        dest_path = "/etc/nftables/rules/internet.nft"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nft", delete=False) as f:
            f.write(rules)
            tmp_path = f.name
        try:
            await self.docker.copy_to_container(
                self.gateway_container, tmp_path, dest_path
            )
        except Exception as exc:
            os.unlink(tmp_path)
            raise GatewayError(
                f"Gateway container '{self.gateway_container}' unavailable: {exc}"
            ) from exc
        os.unlink(tmp_path)

        exit_code, output = await self.docker.exec_command(
            self.gateway_container, ["nft", "-f", dest_path]
        )
        if exit_code != 0:
            raise GatewayError(
                f"Failed to apply internet policy ({config.mode.value}): {output}"
            )

    async def remove_internet_policy(self) -> None:
        """Remove internet policy rules (reverts to isolated state)."""
        exit_code, output = await self.docker.exec_command(
            self.gateway_container,
            ["nft", "delete", "table", "inet", "netengine_internet"],
        )
        if exit_code != 0 and "No such table" not in output:
            raise GatewayError(f"Failed to remove internet policy rules: {output}")
        await self.docker.exec_command(
            self.gateway_container,
            ["rm", "-f", "/etc/nftables/rules/internet.nft"],
        )

    def _internet_rules(self, config: "RealInternetConfig") -> str:
        """Dispatch to the correct rule generator for *config.mode*."""
        from netengine.spec.types import GatewayRealInternetMode

        dispatch = {
            GatewayRealInternetMode.ISOLATED: self._isolated_internet_rules,
            GatewayRealInternetMode.SHADOWED: self._shadowed_internet_rules,
            GatewayRealInternetMode.MIRRORED: lambda: self._mirrored_internet_rules(
                config
            ),
            GatewayRealInternetMode.EXPOSED: self._exposed_internet_rules,
        }
        return dispatch[config.mode]()

    def _isolated_internet_rules(self) -> str:
        """Block all WAN ingress/egress; pass only internal traffic."""
        return """\
table inet netengine_internet {
    chain input {
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        iifname "lo" accept
        iifname != "eth_wan" accept
    }
    chain output {
        type filter hook output priority 0; policy drop;
        ct state established,related accept
        oifname "lo" accept
        oifname != "eth_wan" accept
    }
    chain forward {
        type filter hook forward priority 0; policy drop;
        iifname "eth_wan" drop
        oifname "eth_wan" drop
    }
}
"""

    def _shadowed_internet_rules(self) -> str:
        """Outbound HTTPS only (read-only shadow); all inbound WAN blocked."""
        return """\
table inet netengine_internet {
    chain input {
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        iifname "lo" accept
        iifname != "eth_wan" accept
    }
    chain forward {
        type filter hook forward priority 0; policy drop;
        ct state established,related accept
        iifname != "eth_wan" oifname "eth_wan" tcp dport { 80, 443 } ct state new accept
        iifname "eth_wan" drop
    }
    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
        oifname "eth_wan" masquerade
    }
}
"""

    def _mirrored_internet_rules(self, config: "RealInternetConfig") -> str:
        """Allow outbound to configured service mirrors; block all other WAN."""
        mirror_accepts = ""
        for mirror in config.service_mirrors:
            # Allow traffic destined for the in-world service counterpart
            mirror_accepts += (
                f"\n        ip daddr {mirror.in_world_service} tcp dport {{ 80, 443 }}"
                " ct state new accept"
            )

        return f"""\
table inet netengine_internet {{
    chain forward {{
        type filter hook forward priority 0; policy drop;
        ct state established,related accept{mirror_accepts}
        iifname "eth_wan" drop
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
        oifname "eth_wan" masquerade
    }}
}}
"""

    def _exposed_internet_rules(self) -> str:
        """Full internet access with stateful inbound filtering."""
        return """\
table inet netengine_internet {
    chain input {
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        iifname "lo" accept
        tcp dport { 80, 443 } ct state new accept
    }
    chain forward {
        type filter hook forward priority 0; policy accept;
        ct state established,related accept
    }
    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
        oifname "eth_wan" masquerade
    }
}
"""

    # ─────────────────────────────────────────────
    # Cross-world peer routing
    # ─────────────────────────────────────────────

    async def apply_peer_routing(self, peer_name: str, peer_endpoint_ip: str) -> None:
        """Allow forwarded traffic to/from a cross-world peer endpoint.

        Adds a dedicated nftables table for the peer so rules can be removed
        cleanly when the peer is removed.
        """
        rules = f"""\
table inet netengine_peer_{peer_name} {{
    chain forward {{
        type filter hook forward priority 0; policy accept;
        ip daddr {peer_endpoint_ip} ct state new accept
        ip saddr {peer_endpoint_ip} ct state new accept
    }}
}}
"""
        dest_path = f"/etc/nftables/rules/peer_{peer_name}.nft"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".nft", delete=False) as f:
            f.write(rules)
            tmp_path = f.name
        try:
            await self.docker.copy_to_container(
                self.gateway_container, tmp_path, dest_path
            )
        except Exception as exc:
            os.unlink(tmp_path)
            raise GatewayError(
                f"Gateway container '{self.gateway_container}' unavailable: {exc}"
            ) from exc
        os.unlink(tmp_path)

        exit_code, output = await self.docker.exec_command(
            self.gateway_container, ["nft", "-f", dest_path]
        )
        if exit_code != 0:
            raise GatewayError(
                f"Failed to apply peer routing for {peer_name}: {output}"
            )

    async def remove_peer_routing(self, peer_name: str) -> None:
        """Remove routing rules for a cross-world peer."""
        exit_code, output = await self.docker.exec_command(
            self.gateway_container,
            ["nft", "delete", "table", "inet", f"netengine_peer_{peer_name}"],
        )
        if exit_code != 0 and "No such table" not in output:
            raise GatewayError(
                f"Failed to remove peer routing for {peer_name}: {output}"
            )
        await self.docker.exec_command(
            self.gateway_container,
            ["rm", "-f", f"/etc/nftables/rules/peer_{peer_name}.nft"],
        )

    # ─────────────────────────────────────────────
    # DHCP (dynamic_ip profile feature)
    # ─────────────────────────────────────────────

    async def setup_dhcp(self, and_name: str, cidr: str, gateway_ip: str) -> None:
        """Write a dnsmasq config for the AND subnet and reload dnsmasq."""
        network = ipaddress.ip_network(cidr, strict=False)
        # Reserve .1 (gateway) and broadcast; hand out .2 through penultimate
        start = str(network.network_address + 2)
        end = str(network.broadcast_address - 1)
        conf = (
            f"interface=eth_{and_name}\n"
            f"dhcp-range={start},{end},12h\n"
            f"dhcp-option=3,{gateway_ip}\n"
            f"dhcp-option=6,{gateway_ip}\n"
        )
        conf_path = f"/etc/dnsmasq.d/{and_name}.conf"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(conf)
            tmp_path = f.name
        try:
            await self.docker.copy_to_container(
                self.gateway_container, tmp_path, conf_path
            )
        finally:
            os.unlink(tmp_path)

        # Signal running dnsmasq to reload; start it if not yet running.
        exit_code, _ = await self.docker.exec_command(
            self.gateway_container, ["pkill", "-SIGHUP", "dnsmasq"]
        )
        if exit_code != 0:
            _, err = await self.docker.exec_command(
                self.gateway_container,
                ["dnsmasq", "--conf-dir=/etc/dnsmasq.d", "--keep-in-foreground"],
            )
            if err and "already" not in err.lower():
                raise GatewayError(f"Failed to start dnsmasq for {and_name}: {err}")

    async def remove_dhcp(self, and_name: str) -> None:
        """Remove dnsmasq config for the AND and reload."""
        await self.docker.exec_command(
            self.gateway_container, ["rm", "-f", f"/etc/dnsmasq.d/{and_name}.conf"]
        )
        await self.docker.exec_command(
            self.gateway_container, ["pkill", "-SIGHUP", "dnsmasq"]
        )

    # ─────────────────────────────────────────────
    # BGP speaker (bgp profile feature)
    # ─────────────────────────────────────────────

    async def setup_bgp(
        self, and_name: str, cidr: str, gateway_ip: str, bgp_mode: str
    ) -> None:
        """Provision a Bird2 BGP speaker sidecar container for the AND."""
        bird_conf = self._bird_conf(and_name, cidr, gateway_ip)
        conf_path = f"/etc/bird/bird_{and_name}.conf"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(bird_conf)
            tmp_path = f.name

        container_name = f"netengine_bgp_{and_name}"
        bridge_name = f"netengines_and_{and_name}"
        try:
            await self.docker.start_container(
                name=container_name,
                image="pierrecdn/bird:2.0.9",
                command=["bird", "-c", "/etc/bird/bird.conf", "-f"],
                volumes={},
                network=bridge_name,
                ip=str(ipaddress.ip_network(cidr, strict=False).network_address + 2),
                environment={},
            )
        except Exception as exc:
            os.unlink(tmp_path)
            if bgp_mode == "required":
                raise GatewayError(
                    f"BGP speaker required but failed to start for {and_name}: {exc}"
                ) from exc
            return

        try:
            await self.docker.copy_to_container(container_name, tmp_path, conf_path)
        finally:
            os.unlink(tmp_path)

        await self.docker.exec_command(container_name, ["birdc", "configure"])

    async def remove_bgp(self, and_name: str) -> None:
        """Stop and remove the BGP speaker sidecar for the AND."""
        container_name = f"netengine_bgp_{and_name}"
        try:
            await self.docker.stop_container(container_name)
        except Exception:
            pass

    def _bird_conf(self, and_name: str, cidr: str, gateway_ip: str) -> str:
        return f"""\
router id {gateway_ip};

protocol device {{}}

protocol direct {{
    ipv4;
}}

protocol kernel {{
    ipv4 {{
        export all;
    }};
}}

protocol static netengine_{and_name} {{
    ipv4;
    route {cidr} blackhole;
}}
"""
