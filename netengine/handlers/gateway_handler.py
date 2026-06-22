import os
import tempfile

from netengine.gateways.base import BaseGatewayHandler


class GatewayHandler(BaseGatewayHandler):
    def __init__(self, docker):
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
            raise ValueError(f"Unknown AND profile: {profile}")

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
            raise RuntimeError(f"Failed to apply nftables rules for {and_name}: {output}")

    async def remove_rules(self, and_name: str) -> None:
        """Delete the nftables table for this AND."""
        cmd = ["nft", "delete", "table", "ip", f"netengine_{and_name}"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        # Table-not-found is acceptable on teardown
        if exit_code != 0 and "No such table" not in output:
            raise RuntimeError(f"Failed to remove nftables table for {and_name}: {output}")
        cmd = ["rm", "-f", f"/etc/nftables/rules/{and_name}.nft"]
        await self.docker.exec_command(self.gateway_container, cmd)

    async def reload(self) -> None:
        """Reload all nftables rules on the gateway container."""
        cmd = ["nft", "-f", "/etc/nftables/rules/main.nft"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0:
            raise RuntimeError(f"Gateway nftables reload failed: {output}")
