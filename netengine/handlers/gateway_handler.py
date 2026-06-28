import os
import tempfile
from typing import TYPE_CHECKING, Any, Optional

from netengine.errors import GatewayError
from netengine.gateways.base import BaseGatewayHandler

if TYPE_CHECKING:
    from netengine.spec.models import RealInternetConfig


class GatewayHandler(BaseGatewayHandler):
    def __init__(self, docker: Any) -> None:
        self.docker = docker
        self.gateway_container = "netengine_gateway"

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
            await self.docker.copy_to_container(self.gateway_container, tmp_path, dest_path)
        finally:
            os.unlink(tmp_path)

        cmd = ["nft", "-f", dest_path]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0:
            raise GatewayError(f"Failed to apply nftables rules for {and_name}: {output}")

    async def remove_rules(self, and_name: str) -> None:
        """Delete the nftables table for this AND."""
        cmd = ["nft", "delete", "table", "ip", f"netengine_{and_name}"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        # Table-not-found is acceptable on teardown
        if exit_code != 0 and "No such table" not in output:
            raise GatewayError(f"Failed to remove nftables table for {and_name}: {output}")
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
            await self.docker.copy_to_container(self.gateway_container, tmp_path, dest_path)
        finally:
            os.unlink(tmp_path)

        exit_code, output = await self.docker.exec_command(
            self.gateway_container, ["nft", "-f", dest_path]
        )
        if exit_code != 0:
            raise GatewayError(f"Failed to apply internet policy ({config.mode.value}): {output}")

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
            GatewayRealInternetMode.MIRRORED: lambda: self._mirrored_internet_rules(config),
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
                f'\n        ip daddr {mirror.in_world_service} tcp dport {{ 80, 443 }}'
                ' ct state new accept'
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
            await self.docker.copy_to_container(self.gateway_container, tmp_path, dest_path)
        finally:
            os.unlink(tmp_path)

        exit_code, output = await self.docker.exec_command(
            self.gateway_container, ["nft", "-f", dest_path]
        )
        if exit_code != 0:
            raise GatewayError(f"Failed to apply peer routing for {peer_name}: {output}")

    async def remove_peer_routing(self, peer_name: str) -> None:
        """Remove routing rules for a cross-world peer."""
        exit_code, output = await self.docker.exec_command(
            self.gateway_container,
            ["nft", "delete", "table", "inet", f"netengine_peer_{peer_name}"],
        )
        if exit_code != 0 and "No such table" not in output:
            raise GatewayError(f"Failed to remove peer routing for {peer_name}: {output}")
        await self.docker.exec_command(
            self.gateway_container,
            ["rm", "-f", f"/etc/nftables/rules/peer_{peer_name}.nft"],
        )
