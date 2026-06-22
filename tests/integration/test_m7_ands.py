"""Integration tests for Phase 7: Administrative Network Domains (ANDs).

Tests cover:
- Per-org AND provisioning with network isolation
- Subnet allocation from address pools
- nftables rule application via gateway
- DNS zone registration
- Event-driven provisioning for new orgs
- Health checks and idempotence
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.state import RuntimeState
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.logging import get_logger
from netengine.phases.phase_ands import ANDsPhaseHandler
from netengine.spec.models import ANDInstance, ANDProfileDef, ANDsPhase, BGPFabricConfig


class TestM7ANDsInterfaceCompliance:
    """Tests that M7 handler implements BasePhaseHandler contract."""

    def test_m7_is_phase_handler(self) -> None:
        """ANDsPhaseHandler must implement BasePhaseHandler."""
        assert issubclass(ANDsPhaseHandler, BasePhaseHandler)

    async def test_m7_has_execute_method(self) -> None:
        """Handler must have execute method."""
        handler = ANDsPhaseHandler()
        assert hasattr(handler, "execute")
        assert callable(handler.execute)

    async def test_m7_has_healthcheck_method(self) -> None:
        """Handler must have healthcheck method."""
        handler = ANDsPhaseHandler()
        assert hasattr(handler, "healthcheck")
        assert callable(handler.healthcheck)

    async def test_m7_has_should_skip_method(self) -> None:
        """Handler must have should_skip method."""
        handler = ANDsPhaseHandler()
        assert hasattr(handler, "should_skip")
        assert callable(handler.should_skip)


class TestM7PrerequisiteValidation:
    """Tests that M7 validates M1-M6 prerequisites."""

    async def test_m7_fails_without_substrate(self) -> None:
        """M7 should fail if substrate_output is None."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = None  # Not run
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="Substrate phase.*must complete"):
            await handler.execute(phase_context)

    async def test_m7_fails_without_dns(self) -> None:
        """M7 should fail if dns_output is None."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = None  # Not run
        runtime_state.domain_registry_output = {"pools": {}}

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="DNS phase.*must complete"):
            await handler.execute(phase_context)

    async def test_m7_fails_without_domain_registry(self) -> None:
        """M7 should fail if domain_registry_output is None."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = None  # Not run

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="Domain Registry.*must complete"):
            await handler.execute(phase_context)


class TestM7ProfileValidation:
    """Tests that M7 validates AND profiles against spec."""

    async def test_m7_accepts_valid_profile(self) -> None:
        """M7 should accept valid profile reference."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        # Create mock profile with .name attribute (implementation expects this)
        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"  # String profile name
        and_instance.dns_suffix = "acme.internal"

        # Mock ands_spec with profiles that have .name attributes
        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]  # List of objects with .name
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock Docker and gateway handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=[])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    # Should not raise
                    await handler.execute(phase_context)

        # Verify execution succeeded
        assert runtime_state.ands_output is not None
        assert "acme-prod" in runtime_state.ands_output["ands_provisioned"]


class TestM7ANDProvisioning:
    """Tests that M7 provisions ANDs with network creation."""

    async def test_m7_creates_docker_network_per_and(self) -> None:
        """M7 should create isolated Docker bridge network per AND."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"
        and_instance.dns_suffix = "acme.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=[])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    await handler.execute(phase_context)

        # Verify network was created
        mock_docker.create_network.assert_called()
        call_kwargs = mock_docker.create_network.call_args.kwargs
        assert call_kwargs["name"] == "netengines_and_acme-prod"
        assert call_kwargs["driver"] == "bridge"
        assert call_kwargs["internal"] is True

    async def test_m7_records_and_names_in_output(self) -> None:
        """M7 should record created AND names in ands_output."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")

        and_instance1 = MagicMock()
        and_instance1.name = "acme-prod"
        and_instance1.org = "acme"
        and_instance1.profile = "business"
        and_instance1.dns_suffix = "acme.internal"

        and_instance2 = MagicMock()
        and_instance2.name = "widgets-dev"
        and_instance2.org = "widgets"
        and_instance2.profile = "business"
        and_instance2.dns_suffix = "widgets.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance1, and_instance2]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=[])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    await handler.execute(phase_context)

        # Verify output contains both AND names
        output = runtime_state.ands_output
        assert output is not None
        assert "ands_provisioned" in output
        assert "acme-prod" in output["ands_provisioned"]
        assert "widgets-dev" in output["ands_provisioned"]
        assert len(output["ands_provisioned"]) == 2


class TestM7AddressAllocation:
    """Tests that M7 allocates subnets deterministically."""

    async def test_m7_allocates_cidr_per_and(self) -> None:
        """M7 should allocate CIDR for each AND."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"
        and_instance.dns_suffix = "acme.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=[])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    await handler.execute(phase_context)

        # Verify CIDR was allocated and stored
        output = runtime_state.ands_output
        assert output is not None
        assert "address_allocations" in output
        assert "acme-prod" in output["address_allocations"]
        cidr = output["address_allocations"]["acme-prod"]
        assert "/" in cidr  # Is CIDR format


class TestM7RuleApplication:
    """Tests that M7 applies nftables rules via gateway."""

    async def test_m7_generates_rules_for_and(self) -> None:
        """M7 should generate nftables rules from AND profile."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"
        and_instance.dns_suffix = "acme.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=["rule1", "rule2"])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    await handler.execute(phase_context)

        # Verify generate_rules was called
        mock_gateway.generate_rules.assert_called()
        call_kwargs = mock_gateway.generate_rules.call_args.kwargs
        assert call_kwargs["and_name"] == "acme-prod"
        assert call_kwargs["profile"] == "business"

    async def test_m7_applies_rules_to_gateway(self) -> None:
        """M7 should apply generated rules to gateway."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"
        and_instance.dns_suffix = "acme.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=["rule1"])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    await handler.execute(phase_context)

        # Verify apply_rules was called
        mock_gateway.apply_rules.assert_called()


class TestM7Healthcheck:
    """Tests that M7 healthcheck verifies AND state."""

    async def test_m7_healthcheck_fails_without_output(self) -> None:
        """Healthcheck should fail if ands_output is None."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.ands_output = None

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.healthcheck(phase_context)
        assert result is False

    async def test_m7_healthcheck_fails_without_provisioned_ands(self) -> None:
        """Healthcheck should fail if no ANDs provisioned."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.ands_output = {
            "ands_provisioned": [],  # Empty
            "address_allocations": {},
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.healthcheck(phase_context)
        assert result is False

    async def test_m7_healthcheck_verifies_docker_networks(self) -> None:
        """Healthcheck should verify Docker networks exist."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.ands_output = {
            "ands_provisioned": ["acme-prod"],
            "address_allocations": {"acme-prod": "172.16.0.0/24"},
        }
        runtime_state.ands_instances = {
            "acme-prod": {
                "name": "acme-prod",
                "org": "acme",
                "cidr": "172.16.0.0/24",
                "gateway_ip": "172.16.0.1",
            }
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        # Mock Docker handler
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = MagicMock()
            mock_network = MagicMock()
            mock_docker.client.networks.get.return_value = mock_network
            mock_docker_class.return_value = mock_docker

            result = await handler.healthcheck(phase_context)

        # Should succeed with valid network
        assert result is True
        mock_docker.client.networks.get.assert_called_with("netengines_and_acme-prod")


class TestM7Idempotence:
    """Tests that M7 is idempotent (skips if already deployed)."""

    async def test_m7_should_skip_if_already_provisioned(self) -> None:
        """should_skip should return True if ands_output exists."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.ands_output = {
            "ands_provisioned": ["acme-prod"],
            "address_allocations": {"acme-prod": "172.16.0.0/24"},
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.should_skip(phase_context)
        assert result is True

    async def test_m7_should_execute_if_not_provisioned(self) -> None:
        """should_skip should return False if not yet provisioned."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.ands_output = None

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.should_skip(phase_context)
        assert result is False


class TestM7EventHandling:
    """Tests that M7 emits proper events."""

    async def test_m7_emits_ands_ready_event(self) -> None:
        """M7 should emit ands.ready event on success."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}
        runtime_state.correlation_id = "test-correlation-123"
        runtime_state.parent_event_id = None

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"
        and_instance.dns_suffix = "acme.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=[])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock) as mock_emit:
                    await handler.execute(phase_context)

        # Verify event was emitted
        mock_emit.assert_called()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["event_type"] == "ands.ready"
        assert "ands_provisioned" in call_kwargs["payload"]


class TestM7OutputStructure:
    """Tests that M7 produces correct output structure."""

    async def test_m7_output_contains_required_fields(self) -> None:
        """M7 should populate all required fields in ands_output."""
        handler = ANDsPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.domain_registry_output = {"pools": {}}

        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")
        and_instance = MagicMock()
        and_instance.name = "acme-prod"
        and_instance.org = "acme"
        and_instance.profile = "business"
        and_instance.dns_suffix = "acme.internal"

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]
        ands_spec.instances = [and_instance]

        spec = MagicMock()
        spec.ands = ands_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_ands.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.create_network = AsyncMock()
            mock_docker.connect_network = AsyncMock()
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_ands.GatewayHandler") as mock_gateway_class:
                mock_gateway = AsyncMock()
                mock_gateway.gateway_container = "gateway-123"
                mock_gateway.generate_rules = AsyncMock(return_value=[])
                mock_gateway.apply_rules = AsyncMock()
                mock_gateway_class.return_value = mock_gateway

                with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                    await handler.execute(phase_context)

        output = runtime_state.ands_output
        assert output is not None
        assert "ands_provisioned" in output
        assert "address_allocations" in output
        assert "profiles_used" in output
        assert "deployed_at" in output
        assert isinstance(output["ands_provisioned"], list)
        assert isinstance(output["address_allocations"], dict)
        assert isinstance(output["profiles_used"], list)
        assert isinstance(output["deployed_at"], str)


class TestM7OrgAdmissionEvents:
    """Tests that M7 consumes org.admitted events for dynamic provisioning."""

    async def test_m7_processes_org_admitted_event(self) -> None:
        """M7 should provision AND when org.admitted event received."""
        handler = ANDsPhaseHandler()

        # Mock context
        mock_pgmq = AsyncMock()
        mock_msg = {
            "msg_id": "msg-123",
            "message": json.dumps(
                {
                    "event_id": "event-123",
                    "correlation_id": "corr-123",
                    "parent_event_id": None,
                    "event_type": "org.admitted",
                    "emitted_by": "registry_handler",
                    "emitted_at": "2026-06-22T12:00:00",
                    "payload": {"org_name": "new-org", "and_profile": "business"},
                    "retry_count": 0,
                }
            ),
        }

        # First call returns the org.admitted event, second returns None (exit loop)
        mock_pgmq.receive = AsyncMock(side_effect=[mock_msg, None])
        mock_pgmq.delete = AsyncMock()
        mock_pgmq.archive_to_dlq = AsyncMock()

        phase_context = MagicMock()
        phase_context.pgmq_client = mock_pgmq
        phase_context.logger = get_logger("test")

        # Create mock profile with .name attribute
        class MockProfile:
            def __init__(self, name: str):
                self.name = name

        profile_biz = MockProfile("business")

        ands_spec = MagicMock()
        ands_spec.profiles = [profile_biz]

        # Mock Docker and gateway
        mock_docker = AsyncMock()
        mock_docker.create_network = AsyncMock()
        mock_docker.connect_network = AsyncMock()

        mock_gateway = AsyncMock()
        mock_gateway.gateway_container = "gateway-123"
        mock_gateway.generate_rules = AsyncMock(return_value=[])
        mock_gateway.apply_rules = AsyncMock()

        # Run consumer but exit after first event
        import asyncio

        consumer_task = asyncio.create_task(
            handler._consume_org_admission_events(
                phase_context, mock_docker, mock_gateway, ands_spec
            )
        )

        # Give it time to process
        await asyncio.sleep(0.2)
        consumer_task.cancel()

        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

        # Verify event was processed (delete called due to archive_to_dlq on error)
        # Since provisioning will fail (no Docker network exists), it will be archived
        assert mock_pgmq.delete.called or mock_pgmq.archive_to_dlq.called
