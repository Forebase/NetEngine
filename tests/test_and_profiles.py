"""Unit tests for AND profile features: dynamic_ip (DHCP), reverse_dns, bgp."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from netengine.handlers.gateway_handler import GatewayHandler


@pytest.fixture
def mock_docker() -> MagicMock:
    docker = MagicMock()
    docker.copy_to_container = AsyncMock()
    docker.exec_command = AsyncMock(return_value=(0, ""))
    docker.start_container = AsyncMock(return_value="container-id")
    docker.stop_container = AsyncMock()
    return docker


@pytest.fixture
def handler(mock_docker: MagicMock) -> GatewayHandler:
    return GatewayHandler(mock_docker)


class TestSetupDhcp:
    async def test_writes_conf_to_container(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.setup_dhcp("lab1", "172.16.1.0/24", "172.16.1.1")
        mock_docker.copy_to_container.assert_called_once()
        dest = mock_docker.copy_to_container.call_args[0][2]
        assert dest == "/etc/dnsmasq.d/lab1.conf"

    async def test_dhcp_range_excludes_gateway_and_broadcast(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        written: list[str] = []

        async def capture(container: str, src: str, dest: str) -> None:
            with open(src) as f:
                written.append(f.read())

        mock_docker.copy_to_container.side_effect = capture
        await handler.setup_dhcp("lab1", "172.16.1.0/24", "172.16.1.1")
        conf = written[0]
        assert "dhcp-range=172.16.1.2,172.16.1.254,12h" in conf
        assert "dhcp-option=3,172.16.1.1" in conf
        assert "dhcp-option=6,172.16.1.1" in conf

    async def test_signals_dnsmasq_reload(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.setup_dhcp("lab1", "172.16.1.0/24", "172.16.1.1")
        cmd_args = [c[0][1] for c in mock_docker.exec_command.call_args_list]
        assert ["pkill", "-SIGHUP", "dnsmasq"] in cmd_args

    async def test_starts_dnsmasq_if_pkill_fails(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        mock_docker.exec_command.side_effect = [
            (1, "no process found"),  # pkill fails
            (0, ""),  # start dnsmasq
        ]
        await handler.setup_dhcp("lab1", "172.16.1.0/24", "172.16.1.1")
        calls = [c[0][1] for c in mock_docker.exec_command.call_args_list]
        assert any("dnsmasq" in c[0] for c in calls)


class TestRemoveDhcp:
    async def test_removes_conf_and_reloads(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.remove_dhcp("lab1")
        rm_cmd = mock_docker.exec_command.call_args_list[0][0][1]
        assert rm_cmd == ["rm", "-f", "/etc/dnsmasq.d/lab1.conf"]


class TestSetupBgp:
    async def test_starts_bird2_container(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.setup_bgp("lab1", "172.16.1.0/24", "172.16.1.1", "optional")
        mock_docker.start_container.assert_called_once()
        name_arg = mock_docker.start_container.call_args[1]["name"]
        assert name_arg == "netengine_bgp_lab1"

    async def test_bird_conf_contains_cidr(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        written: list[str] = []

        async def capture(container: str, src: str, dest: str) -> None:
            with open(src) as f:
                written.append(f.read())

        mock_docker.copy_to_container.side_effect = capture
        await handler.setup_bgp("lab1", "172.16.1.0/24", "172.16.1.1", "optional")
        assert written, "Bird config should have been written"
        assert "172.16.1.0/24" in written[0]

    async def test_optional_bgp_does_not_raise_on_start_failure(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        mock_docker.start_container.side_effect = RuntimeError("image not found")
        # Should not raise for optional mode
        await handler.setup_bgp("lab1", "172.16.1.0/24", "172.16.1.1", "optional")

    async def test_required_bgp_raises_on_start_failure(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        from netengine.errors import GatewayError

        mock_docker.start_container.side_effect = RuntimeError("image not found")
        with pytest.raises(GatewayError, match="BGP speaker required"):
            await handler.setup_bgp("lab1", "172.16.1.0/24", "172.16.1.1", "required")


class TestRemoveBgp:
    async def test_stops_container(self, handler: GatewayHandler, mock_docker: MagicMock) -> None:
        await handler.remove_bgp("lab1")
        mock_docker.stop_container.assert_called_once_with("netengine_bgp_lab1")

    async def test_tolerates_stop_failure(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        mock_docker.stop_container.side_effect = RuntimeError("not found")
        # Should not raise
        await handler.remove_bgp("lab1")


class TestAddReverseZone:
    """DNSHandler.add_reverse_zone creates a PTR zone for the AND subnet."""

    def _make_context(self, dns_output: object, tmp_path: object = None) -> MagicMock:
        import pathlib

        ctx = MagicMock()
        ctx.runtime_state.dns_output = dns_output
        ctx.mock_mode = True
        ctx.docker_client = None
        # add_zone_record always calls Path(context.zone_dir); point at a temp dir
        ctx.zone_dir = str(tmp_path) if tmp_path else "/tmp/nonexistent-zone-dir-xyzzy"
        ctx.logger = MagicMock()
        return ctx

    async def test_creates_reverse_zone_entry(self, tmp_path: object) -> None:
        from netengine.handlers.dns import DNSHandler

        dns = DNSHandler()
        dns_output: dict = {"zone_files": {}}
        ctx = self._make_context(dns_output, tmp_path)

        await dns.add_reverse_zone(ctx, "172.16.1.0/24", "172.16.1.1")

        assert "1.16.172.in-addr.arpa" in dns_output["zone_files"]

    async def test_gateway_ptr_record_added(self, tmp_path: object) -> None:
        from netengine.handlers.dns import DNSHandler

        dns = DNSHandler()
        dns_output: dict = {"zone_files": {}}
        ctx = self._make_context(dns_output, tmp_path)

        await dns.add_reverse_zone(ctx, "172.16.1.0/24", "172.16.1.1")

        zone_content = dns_output["zone_files"]["1.16.172.in-addr.arpa"]
        assert "PTR" in zone_content

    async def test_skips_gracefully_when_dns_output_missing(self) -> None:
        from netengine.handlers.dns import DNSHandler

        dns = DNSHandler()
        ctx = self._make_context(None)
        ctx.runtime_state.dns_output = None
        # Should not raise
        await dns.add_reverse_zone(ctx, "172.16.1.0/24", "172.16.1.1")


class TestFeatureGatePromoted:
    """Confirm dynamic_ip, reverse_dns, bgp are now experimental (not unsupported)."""

    def test_dynamic_ip_is_experimental(self) -> None:
        from netengine.spec.feature_state import FEATURE_STATE_REGISTRY

        entry = next(e for e in FEATURE_STATE_REGISTRY if e.path == "ands.profiles.*.dynamic_ip")
        assert entry.state == "experimental"

    def test_reverse_dns_is_experimental(self) -> None:
        from netengine.spec.feature_state import FEATURE_STATE_REGISTRY

        entry = next(e for e in FEATURE_STATE_REGISTRY if e.path == "ands.profiles.*.reverse_dns")
        assert entry.state == "experimental"

    def test_bgp_is_experimental(self) -> None:
        from netengine.spec.feature_state import FEATURE_STATE_REGISTRY

        entry = next(e for e in FEATURE_STATE_REGISTRY if e.path == "ands.profiles.*.bgp")
        assert entry.state == "experimental"
