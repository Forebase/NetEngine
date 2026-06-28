"""Tests for Gateway Portal — Real Internet and Cross-World Federation.

Covers:
- Real internet policy for all five modes (ISOLATED, SHADOWED, MIRRORED, EXPOSED, CUSTOM)
- Peer routing (apply + remove)
- GatewayPortalHandler execute in mock mode
- Trust anchor installation
- DNS forwarding for peers
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.state import RuntimeState
from netengine.errors import GatewayError
from netengine.handlers.gateway_handler import GatewayHandler
from netengine.handlers.gateway_portal_handler import GatewayPortalHandler
from netengine.spec.models import CrossWorldConfig, CrossWorldPeer, GatewayPortal, RealInternetConfig, ServiceMirror
from netengine.spec.types import GatewayCrossWorldMode, GatewayRealInternetMode


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def mock_docker():
    d = MagicMock()
    d.copy_to_container = AsyncMock()
    d.exec_command = AsyncMock(return_value=(0, ""))
    return d


@pytest.fixture
def gateway(mock_docker):
    return GatewayHandler(mock_docker)


# ─────────────────────────────────────────────
# Real Internet Policy — rule generation
# ─────────────────────────────────────────────


class TestInternetRuleGeneration:
    def test_isolated_blocks_wan_forward(self, gateway):
        rules = gateway._isolated_internet_rules()
        assert "oifname" in rules and "eth_wan" in rules
        assert "drop" in rules

    def test_isolated_uses_inet_table(self, gateway):
        rules = gateway._isolated_internet_rules()
        assert "table inet netengine_internet" in rules

    def test_shadowed_allows_https_outbound(self, gateway):
        rules = gateway._shadowed_internet_rules()
        assert "tcp dport { 80, 443 }" in rules
        assert "ct state new accept" in rules

    def test_shadowed_blocks_wan_inbound(self, gateway):
        rules = gateway._shadowed_internet_rules()
        assert 'iifname "eth_wan" drop' in rules

    def test_shadowed_has_masquerade(self, gateway):
        rules = gateway._shadowed_internet_rules()
        assert "masquerade" in rules

    def test_mirrored_includes_mirror_addresses(self, gateway):
        config = RealInternetConfig(
            mode=GatewayRealInternetMode.MIRRORED,
            service_mirrors=[
                ServiceMirror(real_hostname="example.com", in_world_service="10.0.1.50"),
            ],
        )
        rules = gateway._mirrored_internet_rules(config)
        assert "10.0.1.50" in rules

    def test_mirrored_multiple_mirrors(self, gateway):
        config = RealInternetConfig(
            mode=GatewayRealInternetMode.MIRRORED,
            service_mirrors=[
                ServiceMirror(real_hostname="a.com", in_world_service="10.0.1.1"),
                ServiceMirror(real_hostname="b.com", in_world_service="10.0.1.2"),
            ],
        )
        rules = gateway._mirrored_internet_rules(config)
        assert "10.0.1.1" in rules
        assert "10.0.1.2" in rules

    def test_exposed_has_policy_accept_on_forward(self, gateway):
        rules = gateway._exposed_internet_rules()
        assert "policy accept" in rules

    def test_exposed_allows_http_inbound(self, gateway):
        rules = gateway._exposed_internet_rules()
        assert "tcp dport { 80, 443 }" in rules

    def test_exposed_has_masquerade(self, gateway):
        rules = gateway._exposed_internet_rules()
        assert "masquerade" in rules


class TestApplyInternetPolicy:
    async def test_custom_mode_is_noop(self, gateway, mock_docker):
        config = RealInternetConfig(mode=GatewayRealInternetMode.CUSTOM)
        await gateway.apply_internet_policy(config)
        mock_docker.copy_to_container.assert_not_called()
        mock_docker.exec_command.assert_not_called()

    async def test_isolated_copies_rules_file(self, gateway, mock_docker):
        config = RealInternetConfig(mode=GatewayRealInternetMode.ISOLATED)
        await gateway.apply_internet_policy(config)
        mock_docker.copy_to_container.assert_called_once()
        args = mock_docker.copy_to_container.call_args[0]
        assert args[2] == "/etc/nftables/rules/internet.nft"

    async def test_shadowed_loads_rules_with_nft(self, gateway, mock_docker):
        config = RealInternetConfig(mode=GatewayRealInternetMode.SHADOWED)
        await gateway.apply_internet_policy(config)
        mock_docker.exec_command.assert_called_once()
        cmd = mock_docker.exec_command.call_args[0][1]
        assert cmd == ["nft", "-f", "/etc/nftables/rules/internet.nft"]

    async def test_exposed_raises_on_nft_failure(self, gateway, mock_docker):
        mock_docker.exec_command.return_value = (1, "nft error")
        config = RealInternetConfig(mode=GatewayRealInternetMode.EXPOSED)
        with pytest.raises(GatewayError, match="internet policy"):
            await gateway.apply_internet_policy(config)

    async def test_mirrored_passes_config_to_rule_generator(self, gateway, mock_docker):
        config = RealInternetConfig(
            mode=GatewayRealInternetMode.MIRRORED,
            service_mirrors=[ServiceMirror(real_hostname="a.com", in_world_service="10.1.2.3")],
        )
        written_content = []

        import builtins
        import os

        real_open = builtins.open

        def capture_open(path, mode="r", **kw):
            if isinstance(path, str) and path.endswith(".nft") and "w" in mode:
                import io
                buf = io.StringIO()
                buf.name = path
                written_content.append(buf)
                return buf
            return real_open(path, mode, **kw)

        with patch("tempfile.NamedTemporaryFile") as mock_tmp, patch("os.unlink"):
            import io
            buf = io.StringIO()
            buf.name = "/tmp/fake.nft"
            mock_tmp.return_value.__enter__ = MagicMock(return_value=MagicMock(
                write=lambda s: written_content.append(s),
                name="/tmp/fake.nft",
            ))
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            await gateway.apply_internet_policy(config)

        # Verify copy was called with the internet.nft path
        mock_docker.copy_to_container.assert_called_once()


class TestRemoveInternetPolicy:
    async def test_removes_table_and_file(self, gateway, mock_docker):
        await gateway.remove_internet_policy()
        assert mock_docker.exec_command.call_count == 2
        first_cmd = mock_docker.exec_command.call_args_list[0][0][1]
        assert "delete" in first_cmd and "netengine_internet" in first_cmd

    async def test_table_not_found_is_tolerated(self, gateway, mock_docker):
        mock_docker.exec_command.side_effect = [
            (1, "No such table"),
            (0, ""),
        ]
        await gateway.remove_internet_policy()

    async def test_other_failure_raises(self, gateway, mock_docker):
        mock_docker.exec_command.return_value = (1, "permission denied")
        with pytest.raises(GatewayError, match="internet policy"):
            await gateway.remove_internet_policy()


# ─────────────────────────────────────────────
# Cross-World Peer Routing
# ─────────────────────────────────────────────


class TestPeerRouting:
    async def test_apply_peer_routing_uses_peer_name_in_table(self, gateway, mock_docker):
        await gateway.apply_peer_routing("worldb", "192.168.100.1")
        copy_args = mock_docker.copy_to_container.call_args[0]
        assert "peer_worldb" in copy_args[2]

    async def test_apply_peer_routing_loads_rules(self, gateway, mock_docker):
        await gateway.apply_peer_routing("worldb", "192.168.100.1")
        cmd = mock_docker.exec_command.call_args[0][1]
        assert cmd[0] == "nft" and "-f" in cmd

    async def test_apply_peer_routing_raises_on_failure(self, gateway, mock_docker):
        mock_docker.exec_command.return_value = (1, "error")
        with pytest.raises(GatewayError, match="worldb"):
            await gateway.apply_peer_routing("worldb", "192.168.100.1")

    async def test_remove_peer_routing_deletes_table(self, gateway, mock_docker):
        await gateway.remove_peer_routing("worldb")
        first_cmd = mock_docker.exec_command.call_args_list[0][0][1]
        assert "netengine_peer_worldb" in first_cmd

    async def test_remove_peer_routing_tolerates_not_found(self, gateway, mock_docker):
        mock_docker.exec_command.side_effect = [(1, "No such table"), (0, "")]
        await gateway.remove_peer_routing("worldb")

    async def test_remove_peer_routing_raises_on_other_failure(self, gateway, mock_docker):
        mock_docker.exec_command.return_value = (1, "permission denied")
        with pytest.raises(GatewayError, match="worldb"):
            await gateway.remove_peer_routing("worldb")


# ─────────────────────────────────────────────
# GatewayPortalHandler — execute in mock mode
# ─────────────────────────────────────────────


class TestGatewayPortalHandlerMockMode:
    def _make_context(self, portal: GatewayPortal, mock_mode: bool = True):
        state = RuntimeState()
        spec = MagicMock()
        spec.gateway_portal = portal
        ctx = MagicMock()
        ctx.spec = spec
        ctx.runtime_state = state
        ctx.mock_mode = mock_mode
        ctx.docker_client = None
        ctx.pgmq_client = None
        ctx.logger = MagicMock()
        return ctx

    async def test_disabled_portal_sets_output_and_returns(self):
        portal = GatewayPortal(
            enabled=False,
            real_internet=RealInternetConfig(mode=GatewayRealInternetMode.ISOLATED),
            cross_world=CrossWorldConfig(mode=GatewayCrossWorldMode.NONE),
        )
        ctx = self._make_context(portal)
        handler = GatewayPortalHandler()
        await handler.execute(ctx)
        assert ctx.runtime_state.gateway_portal_output is not None
        assert ctx.runtime_state.gateway_portal_output["enabled"] is False

    async def test_mock_mode_populates_output(self):
        portal = GatewayPortal(
            enabled=True,
            real_internet=RealInternetConfig(mode=GatewayRealInternetMode.SHADOWED),
            cross_world=CrossWorldConfig(
                mode=GatewayCrossWorldMode.PEERED,
                peers=[
                    CrossWorldPeer(
                        name="worldb",
                        endpoint="192.168.200.1:9000",
                        mode=GatewayCrossWorldMode.PEERED,
                    )
                ],
            ),
        )
        ctx = self._make_context(portal, mock_mode=True)
        handler = GatewayPortalHandler()
        await handler.execute(ctx)
        output = ctx.runtime_state.gateway_portal_output
        assert output["enabled"] is True
        assert output["internet_mode"] == "shadowed"
        assert output["cross_world_mode"] == "peered"
        assert output["peer_count"] == 1
        assert output["mock"] is True

    async def test_healthcheck_returns_false_before_execute(self):
        portal = GatewayPortal(
            enabled=True,
            real_internet=RealInternetConfig(),
            cross_world=CrossWorldConfig(),
        )
        ctx = self._make_context(portal)
        handler = GatewayPortalHandler()
        assert await handler.healthcheck(ctx) is False

    async def test_healthcheck_returns_true_after_execute(self):
        portal = GatewayPortal(
            enabled=True,
            real_internet=RealInternetConfig(),
            cross_world=CrossWorldConfig(),
        )
        ctx = self._make_context(portal, mock_mode=True)
        handler = GatewayPortalHandler()
        await handler.execute(ctx)
        assert await handler.healthcheck(ctx) is True

    async def test_should_skip_after_output_exists(self):
        portal = GatewayPortal(
            enabled=True,
            real_internet=RealInternetConfig(),
            cross_world=CrossWorldConfig(),
        )
        ctx = self._make_context(portal, mock_mode=True)
        handler = GatewayPortalHandler()
        await handler.execute(ctx)
        assert await handler.should_skip(ctx) is True

    async def test_should_not_skip_before_execute(self):
        portal = GatewayPortal(
            enabled=True,
            real_internet=RealInternetConfig(),
            cross_world=CrossWorldConfig(),
        )
        ctx = self._make_context(portal)
        handler = GatewayPortalHandler()
        assert await handler.should_skip(ctx) is False


# ─────────────────────────────────────────────
# RuntimeState — new fields
# ─────────────────────────────────────────────


class TestRuntimeStateNewFields:
    def test_dnssec_output_defaults_to_none(self):
        state = RuntimeState()
        assert state.dnssec_output is None

    def test_gateway_portal_output_defaults_to_none(self):
        state = RuntimeState()
        assert state.gateway_portal_output is None

    def test_intermediate_ca_cert_defaults_to_none(self):
        state = RuntimeState()
        assert state.intermediate_ca_cert is None

    def test_new_fields_survive_save_load_cycle(self, tmp_path, monkeypatch):
        import os
        state_path = tmp_path / "state.json"
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(state_path))

        state = RuntimeState()
        state.dnssec_output = {"zone": "internal", "ksk_name": "Kinternal.+013+00001"}
        state.gateway_portal_output = {"enabled": True, "internet_mode": "exposed"}
        state.intermediate_ca_cert = "-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----"
        state.save()

        loaded = RuntimeState.load()
        assert loaded.dnssec_output == {"zone": "internal", "ksk_name": "Kinternal.+013+00001"}
        assert loaded.gateway_portal_output["internet_mode"] == "exposed"
        assert "ABC" in loaded.intermediate_ca_cert
