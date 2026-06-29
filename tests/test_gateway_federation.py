"""Tests for WS-C: gateway portal feature-gate promotion and handler behaviour."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
import yaml

from netengine.spec.feature_state import FEATURE_STATE_REGISTRY
from netengine.spec.loader import load_spec

# ── helpers ──────────────────────────────────────────────────────────────────


def _write_spec(tmp_path: Path, overrides: dict) -> Path:
    base = yaml.safe_load((Path(__file__).parent.parent / "examples" / "minimal.yaml").read_text())

    def _merge(a: dict, b: dict) -> None:
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                _merge(a[k], v)
            else:
                a[k] = v

    _merge(base, overrides)
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(base))
    return spec_file


# ── Feature-gate state ────────────────────────────────────────────────────────


class TestFeatureGatesAreExperimental:
    """All 5 gateway_portal entries must be experimental (not unsupported)."""

    EXPECTED_PATHS = {
        "gateway_portal.real_internet.mode",
        "gateway_portal.real_internet.service_mirrors",
        "gateway_portal.real_internet.upstream_resolver_enabled",
        "gateway_portal.cross_world.mode",
        "gateway_portal.cross_world.peers",
    }

    def test_all_five_gates_present(self) -> None:
        paths = {e.path for e in FEATURE_STATE_REGISTRY}
        assert self.EXPECTED_PATHS <= paths

    def test_all_five_gates_are_experimental(self) -> None:
        for entry in FEATURE_STATE_REGISTRY:
            if entry.path in self.EXPECTED_PATHS:
                assert (
                    entry.state == "experimental"
                ), f"{entry.path} must be experimental, got {entry.state!r}"


# ── Positive spec-load tests (gate removal proof) ─────────────────────────────


class TestGatewayPortalSpecLoads:
    """Specs using previously-gated gateway_portal fields must now load (warn only)."""

    def test_real_internet_mode_shadowed_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec_file = _write_spec(
            tmp_path, {"gateway_portal": {"real_internet": {"mode": "shadowed"}}}
        )
        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)
        assert spec.gateway_portal.real_internet.mode.value == "shadowed"
        assert any(
            "gateway_portal.real_internet.mode is experimental in alpha" in r.message
            for r in caplog.records
        )

    def test_real_internet_mode_mirrored_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec_file = _write_spec(
            tmp_path,
            {
                "gateway_portal": {
                    "real_internet": {
                        "mode": "mirrored",
                        "service_mirrors": [
                            {"real_hostname": "api.example.com", "in_world_service": "10.1.2.3"}
                        ],
                    }
                }
            },
        )
        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)
        assert spec.gateway_portal.real_internet.mode.value == "mirrored"

    def test_service_mirrors_spec_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec_file = _write_spec(
            tmp_path,
            {
                "gateway_portal": {
                    "real_internet": {
                        "mode": "mirrored",
                        "service_mirrors": [
                            {"real_hostname": "api.example.com", "in_world_service": "10.1.2.3"}
                        ],
                    }
                }
            },
        )
        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)
        assert len(spec.gateway_portal.real_internet.service_mirrors) == 1
        assert any(
            "gateway_portal.real_internet.service_mirrors is experimental in alpha" in r.message
            for r in caplog.records
        )

    def test_upstream_resolver_enabled_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec_file = _write_spec(
            tmp_path,
            {
                "gateway_portal": {
                    "real_internet": {
                        "upstream_resolver_enabled": True,
                        "upstream_resolver_ip": "8.8.8.8",
                    }
                }
            },
        )
        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)
        assert spec.gateway_portal.real_internet.upstream_resolver_enabled is True
        assert any(
            "gateway_portal.real_internet.upstream_resolver_enabled is experimental in alpha"
            in r.message
            for r in caplog.records
        )

    def test_cross_world_peered_mode_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        spec_file = _write_spec(
            tmp_path,
            {
                "gateway_portal": {
                    "cross_world": {
                        "mode": "peered",
                        "peers": [{"name": "world-b", "endpoint": "10.0.0.1:8443"}],
                    }
                }
            },
        )
        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)
        assert spec.gateway_portal.cross_world.mode.value == "peered"
        assert len(spec.gateway_portal.cross_world.peers) == 1
        assert any(
            "gateway_portal.cross_world.mode is experimental in alpha" in r.message
            for r in caplog.records
        )


# ── Handler unit tests ────────────────────────────────────────────────────────


@pytest.fixture
def mock_docker() -> MagicMock:
    docker = MagicMock()
    docker.copy_to_container = AsyncMock()
    docker.exec_command = AsyncMock(return_value=(0, ""))
    docker.signal_container = AsyncMock()
    return docker


class TestApplyInternetPolicy:
    """GatewayHandler.apply_internet_policy writes correct nft rules."""

    async def test_shadowed_rules_written_to_container(self, mock_docker: MagicMock) -> None:
        from netengine.handlers.gateway_handler import GatewayHandler
        from netengine.spec.models import RealInternetConfig
        from netengine.spec.types import GatewayRealInternetMode

        handler = GatewayHandler(mock_docker)
        config = RealInternetConfig(mode=GatewayRealInternetMode.SHADOWED)
        await handler.apply_internet_policy(config)

        mock_docker.copy_to_container.assert_called_once()
        dest = mock_docker.copy_to_container.call_args[0][2]
        assert dest == "/etc/nftables/rules/internet.nft"

        # Verify nft apply was invoked
        cmd = mock_docker.exec_command.call_args[0][1]
        assert cmd[0] == "nft"

    async def test_shadowed_rules_content(self, mock_docker: MagicMock) -> None:
        from netengine.handlers.gateway_handler import GatewayHandler
        from netengine.spec.models import RealInternetConfig
        from netengine.spec.types import GatewayRealInternetMode

        handler = GatewayHandler(mock_docker)
        config = RealInternetConfig(mode=GatewayRealInternetMode.SHADOWED)

        written_content: list[str] = []

        async def capture(container: str, src: str, dest: str) -> None:
            with open(src) as f:
                written_content.append(f.read())

        mock_docker.copy_to_container.side_effect = capture
        await handler.apply_internet_policy(config)

        assert written_content, "No rules were written"
        rules = written_content[0]
        assert "netengine_internet" in rules
        assert "masquerade" in rules

    async def test_mirrored_rules_include_mirror_ip(self, mock_docker: MagicMock) -> None:
        from netengine.handlers.gateway_handler import GatewayHandler
        from netengine.spec.models import RealInternetConfig, ServiceMirror
        from netengine.spec.types import GatewayRealInternetMode

        handler = GatewayHandler(mock_docker)
        config = RealInternetConfig(
            mode=GatewayRealInternetMode.MIRRORED,
            service_mirrors=[
                ServiceMirror(real_hostname="api.example.com", in_world_service="192.168.50.10")
            ],
        )

        written_content: list[str] = []

        async def capture(container: str, src: str, dest: str) -> None:
            with open(src) as f:
                written_content.append(f.read())

        mock_docker.copy_to_container.side_effect = capture
        await handler.apply_internet_policy(config)

        assert written_content
        assert "192.168.50.10" in written_content[0]

    async def test_custom_mode_is_noop(self, mock_docker: MagicMock) -> None:
        from netengine.handlers.gateway_handler import GatewayHandler
        from netengine.spec.models import RealInternetConfig
        from netengine.spec.types import GatewayRealInternetMode

        handler = GatewayHandler(mock_docker)
        config = RealInternetConfig(mode=GatewayRealInternetMode.CUSTOM)
        await handler.apply_internet_policy(config)

        mock_docker.copy_to_container.assert_not_called()


class TestSetupPeer:
    """GatewayPortalHandler._setup_peer wires routing and DNS forwarding."""

    async def test_setup_peer_calls_apply_peer_routing(self, mock_docker: MagicMock) -> None:
        from types import SimpleNamespace

        from netengine.handlers.gateway_handler import GatewayHandler
        from netengine.handlers.gateway_portal_handler import GatewayPortalHandler
        from netengine.spec.models import CrossWorldPeer
        from netengine.spec.types import GatewayCrossWorldMode

        handler = GatewayPortalHandler()
        gateway = GatewayHandler(mock_docker)

        peer = CrossWorldPeer(
            name="world-b",
            endpoint="10.99.0.1:8443",
            mode=GatewayCrossWorldMode.PEERED,
        )

        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.zone_dir = "/tmp/zones"
        ctx.docker_client = mock_docker

        result = await handler._setup_peer(ctx, gateway, mock_docker, peer)

        assert result["name"] == "world-b"
        assert result["endpoint"] == "10.99.0.1:8443"
        # Routing should be configured (apply_peer_routing copies + exec nft)
        mock_docker.copy_to_container.assert_called_once()
        nft_cmd = mock_docker.exec_command.call_args[0][1]
        assert nft_cmd[0] == "nft"

    async def test_setup_peer_routing_failure_does_not_raise(self, mock_docker: MagicMock) -> None:
        from netengine.handlers.gateway_handler import GatewayHandler
        from netengine.handlers.gateway_portal_handler import GatewayPortalHandler
        from netengine.spec.models import CrossWorldPeer
        from netengine.spec.types import GatewayCrossWorldMode

        handler = GatewayPortalHandler()
        gateway = GatewayHandler(mock_docker)

        peer = CrossWorldPeer(name="world-c", endpoint="10.99.0.2:8443")

        mock_docker.copy_to_container.side_effect = RuntimeError("container gone")

        ctx = MagicMock()
        ctx.logger = MagicMock()
        ctx.zone_dir = "/tmp/zones"
        ctx.docker_client = mock_docker

        result = await handler._setup_peer(ctx, gateway, mock_docker, peer)
        assert result["routing_configured"] is False
        assert "routing_error" in result
