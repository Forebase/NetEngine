"""M1 Integration Tests: Substrate and DNS Phase Handlers.

Tests the execute/healthcheck/should_skip interface for Phase 0 and Phases 1-2.
"""

from datetime import datetime

from netengine.handlers.substrate import SubstrateHandler
from netengine.handlers.dns import DNSHandler
from netengine.handlers.context import PhaseContext


class TestSubstrateHandler:
    """Phase 0: Substrate handler tests."""

    async def test_execute_creates_output(self, phase_context: PhaseContext) -> None:
        """Substrate execute should populate substrate_output."""
        handler = SubstrateHandler()
        await handler.execute(phase_context)

        assert phase_context.runtime_state.substrate_output is not None
        assert "orchestrator" in phase_context.runtime_state.substrate_output
        assert "networks" in phase_context.runtime_state.substrate_output
        assert "gateway" in phase_context.runtime_state.substrate_output
        assert phase_context.runtime_state.completed_at is not None

    async def test_execute_creates_networks(self, phase_context: PhaseContext) -> None:
        """Substrate handler should create all configured networks."""
        handler = SubstrateHandler()
        await handler.execute(phase_context)

        networks = phase_context.runtime_state.substrate_output["networks"]
        assert "platform" in networks
        assert "core" in networks
        assert networks["platform"]["subnet"] == "172.20.0.0/16"
        assert networks["core"]["subnet"] == "10.0.0.0/8"

    async def test_execute_configures_ntp(self, phase_context: PhaseContext) -> None:
        """Substrate handler should configure NTP if enabled."""
        handler = SubstrateHandler()
        await handler.execute(phase_context)

        if phase_context.spec.substrate.ntp.enabled:
            assert "ntp" in phase_context.runtime_state.substrate_output
            assert phase_context.runtime_state.substrate_output["ntp"]["enabled"] is True

    async def test_execute_sets_timestamps(self, phase_context: PhaseContext) -> None:
        """Substrate handler should set started_at and completed_at timestamps."""
        handler = SubstrateHandler()
        before = datetime.utcnow()
        await handler.execute(phase_context)
        after = datetime.utcnow()

        assert phase_context.runtime_state.started_at is not None
        assert phase_context.runtime_state.completed_at is not None
        assert before <= phase_context.runtime_state.started_at <= after
        assert before <= phase_context.runtime_state.completed_at <= after

    async def test_healthcheck_passes_after_execute(self, phase_context: PhaseContext) -> None:
        """Healthcheck should pass after successful execute."""
        handler = SubstrateHandler()
        await handler.execute(phase_context)
        healthy = await handler.healthcheck(phase_context)

        assert healthy is True

    async def test_healthcheck_fails_before_execute(self, phase_context: PhaseContext) -> None:
        """Healthcheck should fail before execute."""
        handler = SubstrateHandler()
        healthy = await handler.healthcheck(phase_context)

        assert healthy is False

    async def test_should_skip_true_after_execute(self, phase_context: PhaseContext) -> None:
        """should_skip should return True after substrate is deployed."""
        handler = SubstrateHandler()
        await handler.execute(phase_context)
        skip = await handler.should_skip(phase_context)

        assert skip is True

    async def test_should_skip_false_before_execute(self, phase_context: PhaseContext) -> None:
        """should_skip should return False before substrate is deployed."""
        handler = SubstrateHandler()
        skip = await handler.should_skip(phase_context)

        assert skip is False


class TestDNSHandler:
    """Phases 1-2: DNS handler tests."""

    async def test_execute_creates_output(self, phase_context: PhaseContext) -> None:
        """DNS execute should populate dns_output."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        assert phase_context.runtime_state.dns_output is not None
        assert "root_zone" in phase_context.runtime_state.dns_output
        assert "platform_zone" in phase_context.runtime_state.dns_output
        assert "zone_files" in phase_context.runtime_state.dns_output
        assert phase_context.runtime_state.completed_at is not None

    async def test_execute_creates_root_zone(self, phase_context: PhaseContext) -> None:
        """DNS handler should setup root zone."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        root = phase_context.runtime_state.dns_output["root_zone"]
        assert root["name"] == "root.internal"
        assert root["enabled"] is True
        assert root["type"] == "authoritative"

    async def test_execute_creates_platform_zone(self, phase_context: PhaseContext) -> None:
        """DNS handler should setup platform zone."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        platform = phase_context.runtime_state.dns_output["platform_zone"]
        assert platform["name"] == "platform.internal"
        assert platform["type"] == "authoritative"

    async def test_execute_creates_tlds(self, phase_context: PhaseContext) -> None:
        """DNS handler should configure TLDs from spec."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        tlds = phase_context.runtime_state.dns_output["tlds"]
        assert len(tlds) == len(phase_context.spec.dns.tlds)
        if phase_context.spec.dns.tlds:
            tld_name = phase_context.spec.dns.tlds[0].name
            assert tld_name in tlds

    async def test_execute_generates_zone_files(self, phase_context: PhaseContext) -> None:
        """DNS handler should generate zone files for all zones."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        zone_files = phase_context.runtime_state.dns_output["zone_files"]
        assert len(zone_files) > 0
        assert "root.internal" in zone_files or "platform.internal" in zone_files

    async def test_zone_files_contain_soa_records(self, phase_context: PhaseContext) -> None:
        """Generated zone files should contain SOA records."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        zone_files = phase_context.runtime_state.dns_output["zone_files"]
        for zone_name, content in zone_files.items():
            assert "SOA" in content, f"Zone {zone_name} missing SOA record"

    async def test_zone_files_contain_ns_records(self, phase_context: PhaseContext) -> None:
        """Generated zone files should contain NS records."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        zone_files = phase_context.runtime_state.dns_output["zone_files"]
        for zone_name, content in zone_files.items():
            assert "NS" in content, f"Zone {zone_name} missing NS record"

    async def test_execute_marks_healthy(self, phase_context: PhaseContext) -> None:
        """DNS handler should mark service as healthy after verify."""
        handler = DNSHandler()
        await handler.execute(phase_context)

        assert phase_context.runtime_state.dns_output["healthy"] is True

    async def test_healthcheck_passes_after_execute(self, phase_context: PhaseContext) -> None:
        """Healthcheck should pass after successful execute."""
        handler = DNSHandler()
        await handler.execute(phase_context)
        healthy = await handler.healthcheck(phase_context)

        assert healthy is True

    async def test_healthcheck_fails_before_execute(self, phase_context: PhaseContext) -> None:
        """Healthcheck should fail before execute."""
        handler = DNSHandler()
        healthy = await handler.healthcheck(phase_context)

        assert healthy is False

    async def test_should_skip_true_after_execute(self, phase_context: PhaseContext) -> None:
        """should_skip should return True after DNS is deployed."""
        handler = DNSHandler()
        await handler.execute(phase_context)
        skip = await handler.should_skip(phase_context)

        assert skip is True

    async def test_should_skip_false_before_execute(self, phase_context: PhaseContext) -> None:
        """should_skip should return False before DNS is deployed."""
        handler = DNSHandler()
        skip = await handler.should_skip(phase_context)

        assert skip is False


class TestSubstrateAndDNSIntegration:
    """Integration tests for M1: Substrate → DNS execution order."""

    async def test_substrate_then_dns(self, phase_context: PhaseContext) -> None:
        """Substrate Phase 0 should execute before DNS Phases 1-2."""
        # Execute Phase 0: Substrate
        substrate = SubstrateHandler()
        await substrate.execute(phase_context)
        assert phase_context.runtime_state.substrate_output is not None

        # Execute Phases 1-2: DNS
        dns = DNSHandler()
        await dns.execute(phase_context)
        assert phase_context.runtime_state.dns_output is not None

        # Both should be marked complete
        assert phase_context.runtime_state.completed_at is not None

    async def test_correlation_ids_preserved(self, phase_context: PhaseContext) -> None:
        """Correlation ID should be same across both handlers."""
        correlation_id = phase_context.runtime_state.correlation_id

        substrate = SubstrateHandler()
        await substrate.execute(phase_context)

        dns = DNSHandler()
        await dns.execute(phase_context)

        # Correlation ID should not change
        assert phase_context.runtime_state.correlation_id == correlation_id
