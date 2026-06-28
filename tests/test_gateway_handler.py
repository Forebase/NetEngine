"""Unit tests for GatewayHandler — nftables rule generation and Docker delegation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from netengine.errors import GatewayError
from netengine.handlers.gateway_handler import GatewayHandler


@pytest.fixture
def mock_docker() -> MagicMock:
    docker = MagicMock()
    docker.copy_to_container = AsyncMock()
    docker.exec_command = AsyncMock(return_value=(0, ""))
    return docker


@pytest.fixture
def handler(mock_docker: MagicMock) -> GatewayHandler:
    return GatewayHandler(mock_docker)


class TestGenerateRules:
    """generate_rules() — pure rule generation, no Docker needed."""

    async def test_residential_contains_table_name(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("home1", "residential", "10.1.0.0/24")
        assert "netengine_home1" in rules

    async def test_residential_drops_intra_and_traffic(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("home1", "residential", "10.1.0.0/24")
        assert 'iifname "eth_home1" oifname "eth_home1" drop' in rules

    async def test_residential_has_masquerade(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("home1", "residential", "10.1.0.0/24")
        assert "masquerade" in rules

    async def test_business_allows_new_outbound(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("biz1", "business", "10.2.0.0/24")
        assert "ct state new accept" in rules

    async def test_business_no_masquerade(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("biz1", "business", "10.2.0.0/24")
        assert "masquerade" not in rules

    async def test_datacenter_policy_accept(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("dc1", "datacenter", "10.3.0.0/24")
        assert "policy accept" in rules

    async def test_airgapped_policy_drop(self, handler: GatewayHandler) -> None:
        rules = await handler.generate_rules("air1", "airgapped", "10.4.0.0/24")
        assert "policy drop" in rules
        assert "masquerade" not in rules
        assert "accept" not in rules

    async def test_unknown_profile_raises_gateway_error(self, handler: GatewayHandler) -> None:
        with pytest.raises(GatewayError, match="Unknown AND profile"):
            await handler.generate_rules("x", "nonexistent", "10.5.0.0/24")

    async def test_and_name_interpolated_in_all_profiles(self, handler: GatewayHandler) -> None:
        for profile in ("residential", "business", "datacenter", "airgapped"):
            rules = await handler.generate_rules("myand", profile, "10.0.0.0/24")
            assert "myand" in rules, f"AND name missing in {profile} rules"


class TestApplyRules:
    """apply_rules() — delegates to docker.copy_to_container + exec_command."""

    async def test_calls_copy_to_container(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.apply_rules("home1", "table ip netengine_home1 {}")
        mock_docker.copy_to_container.assert_called_once()
        args = mock_docker.copy_to_container.call_args[0]
        assert args[0] == "netengine_gateway"
        assert args[2] == "/etc/nftables/rules/home1.nft"

    async def test_calls_nft_exec(self, handler: GatewayHandler, mock_docker: MagicMock) -> None:
        await handler.apply_rules("home1", "table ip netengine_home1 {}")
        mock_docker.exec_command.assert_called_once_with(
            "netengine_gateway", ["nft", "-f", "/etc/nftables/rules/home1.nft"]
        )

    async def test_raises_on_nonzero_exit(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        mock_docker.exec_command.return_value = (1, "syntax error")
        with pytest.raises(Exception, match="home1"):
            await handler.apply_rules("home1", "bad rules")


class TestRemoveRules:
    """remove_rules() — tolerates table-not-found, raises on other failures."""

    async def test_success_calls_exec_twice(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.remove_rules("home1")
        assert mock_docker.exec_command.call_count == 2

    async def test_table_not_found_is_tolerated(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        mock_docker.exec_command.side_effect = [
            (1, "Error: No such table"),
            (0, ""),
        ]
        # Should not raise
        await handler.remove_rules("home1")

    async def test_other_nft_failure_raises(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        mock_docker.exec_command.return_value = (1, "permission denied")
        with pytest.raises(Exception, match="home1"):
            await handler.remove_rules("home1")


class TestReload:
    """reload() — loads main.nft on the gateway container."""

    async def test_calls_nft_with_main_nft(
        self, handler: GatewayHandler, mock_docker: MagicMock
    ) -> None:
        await handler.reload()
        mock_docker.exec_command.assert_called_once_with(
            "netengine_gateway", ["nft", "-f", "/etc/nftables/rules/main.nft"]
        )

    async def test_raises_on_failure(self, handler: GatewayHandler, mock_docker: MagicMock) -> None:
        mock_docker.exec_command.return_value = (1, "reload failed")
        with pytest.raises(Exception, match="reload"):
            await handler.reload()
