import os
import tempfile
from typing import Any, Dict, List

from netengine.errors import GatewayError


class GatewayHandler:
    def __init__(self, docker):
        self.docker = docker
        self.gateway_container = "netengine_gateway"

    async def generate_rules(self, and_name: str, profile: str, cidr: str) -> str:
        """Generate nftables ruleset for the given AND profile."""
        # Profile -> rule templates
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
        # Masquerade outbound, drop unsolicited inbound
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
        # Stateful accept inbound, no lateral movement
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
        # no masquerade, but could add optional NAT
    }}
}}
"""

    def _datacenter_rules(self, and_name: str, cidr: str) -> str:
        # Full inbound, no NAT, allow all
        return f"""
table ip netengine_{and_name} {{
    chain forward {{
        type filter hook forward priority 0; policy accept;
        # Allow all forwarding
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
        # No NAT
    }}
}}
"""

    def _airgapped_rules(self, and_name: str, cidr: str) -> str:
        # Drop all traffic on all interfaces
        return f"""
table ip netengine_{and_name} {{
    chain forward {{
        type filter hook forward priority 0; policy drop;
    }}
}}
"""

    async def apply_rules(self, and_name: str, rules: str) -> None:
        """Write rules to gateway container and reload nftables."""
        # Write rules to a file inside the gateway container
        # We'll use a volume mount to share rules, or use `docker exec` to write.
        # Simpler: `docker exec` with `cat` redirection.
        # We'll write the rules to /etc/nftables/rules/{and_name}.nft
        # Then reload: `nft -f /etc/nftables/rules/{and_name}.nft`
        # But nftables needs to load the whole table atomically.
        # For MVP, we'll just `nft -f` directly.
        # We'll combine all rules into a single file and load.
        # We'll store rules in the container's /etc/nftables/rules/ directory.
        # We can mount a volume or exec.
        # Using exec:
        cmd = ["sh", "-c", f"echo '{rules}' > /etc/nftables/rules/{and_name}.nft"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0:
            raise GatewayError(f"Failed to write rules: {output}")
        # Write rules to a temp file then copy into container to avoid shell injection
        # and multi-line content issues with echo/shell quoting.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".nft", delete=False) as f:
            f.write(rules)
            tmp_path = f.name
        try:
            dest_path = f"/etc/nftables/rules/{and_name}.nft"
            await self.docker.copy_to_container(self.gateway_container, tmp_path, dest_path)
        finally:
            os.unlink(tmp_path)
        # Load the ruleset atomically
        cmd = ["nft", "-f", f"/etc/nftables/rules/{and_name}.nft"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to apply nftables rules for {and_name}: {output}")

    async def remove_rules(self, and_name: str) -> None:
        """Delete the nftables table for this AND."""
        cmd = ["nft", "delete", "table", "ip", f"netengine_{and_name}"]
        exit_code, output = await self.docker.exec_command(self.gateway_container, cmd)
        if exit_code != 0 and "No such file or directory" not in output:
            # Table might not exist, ignore
            pass
        # Also remove the rules file
        cmd = ["rm", "-f", f"/etc/nftables/rules/{and_name}.nft"]
        await self.docker.exec_command(self.gateway_container, cmd)
