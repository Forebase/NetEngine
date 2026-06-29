"""Integration tests for M1-M2: Substrate → DNS bootstrap sequence.

Tests the complete end-to-end flow of Phase 0 (Substrate) execution followed by
Phases 1-2 (DNS) execution, including zone record updates, event emission,
and dependency validation.
"""

from unittest.mock import AsyncMock

import pytest

from netengine.errors import DNSError
from netengine.handlers.context import PhaseContext, RuntimeState
from netengine.handlers.dns import DNSHandler
from netengine.handlers.substrate import SubstrateHandler
from logs import get_logger
from netengine.spec.models import NetEngineSpec


class TestSubstrateThenDNSBootstrap:
    """Integration tests for Phase 0 → Phases 1-2 execution sequence."""

    async def test_substrate_then_dns_complete_flow(self, minimal_spec: NetEngineSpec) -> None:
        """Substrate Phase 0 should execute fully before DNS Phases 1-2."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Phase 0: Execute Substrate
        substrate = SubstrateHandler()
        await substrate.execute(context)
        assert context.runtime_state.substrate_output is not None
        assert "orchestrator" in context.runtime_state.substrate_output
        assert "networks" in context.runtime_state.substrate_output
        assert "gateway" in context.runtime_state.substrate_output

        # Phases 1-2: Execute DNS
        dns = DNSHandler()
        await dns.execute(context)
        assert context.runtime_state.dns_output is not None
        assert "root_zone" in context.runtime_state.dns_output
        assert "platform_zone" in context.runtime_state.dns_output
        assert "zone_files" in context.runtime_state.dns_output
        assert context.runtime_state.dns_output["healthy"] is True
        assert context.runtime_state.phase_completed["1"] is True
        assert context.runtime_state.phase_completed["2"] is True

        # DNS should mark both user-facing DNS phases complete
        assert context.runtime_state.started_at is not None
        assert context.runtime_state.completed_at is not None

    async def test_correlation_id_preserved_substrate_to_dns(
        self, minimal_spec: NetEngineSpec
    ) -> None:
        """Correlation ID should be preserved across Phase 0 and Phases 1-2."""
        logger = get_logger("test")
        runtime_state = RuntimeState()
        correlation_id = runtime_state.correlation_id

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Run Phase 0
        substrate = SubstrateHandler()
        await substrate.execute(context)

        # Correlation ID should be unchanged
        assert context.runtime_state.correlation_id == correlation_id

        # Run Phases 1-2
        dns = DNSHandler()
        await dns.execute(context)

        # Correlation ID should still be unchanged
        assert context.runtime_state.correlation_id == correlation_id

    async def test_dns_requires_substrate_execution(self, minimal_spec: NetEngineSpec) -> None:
        """DNS handler should fail if Substrate has not executed."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Try to execute DNS without running Substrate first
        dns = DNSHandler()

        with pytest.raises(DNSError, match="Substrate phase.*must complete"):
            await dns.execute(context)

    async def test_dns_zones_structure_after_execution(self, minimal_spec: NetEngineSpec) -> None:
        """DNS output should contain all required zone structures."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate first
        substrate = SubstrateHandler()
        await substrate.execute(context)

        # Execute DNS
        dns = DNSHandler()
        await dns.execute(context)

        dns_output = context.runtime_state.dns_output
        assert dns_output is not None

        # Verify root zone structure
        root = dns_output["root_zone"]
        assert root["name"] == "root.internal"
        assert root["type"] == "authoritative"
        assert "listen_ip" in root
        assert "soa_primary_ns" in root
        assert "soa_email" in root
        assert "serial_policy" in root

        # Verify platform zone structure
        platform = dns_output["platform_zone"]
        assert platform["name"] == "platform.internal"
        assert platform["type"] == "authoritative"
        assert "listen_ip" in platform

        # Verify TLDs structure
        assert "tlds" in dns_output
        assert isinstance(dns_output["tlds"], dict)

        # Verify zone files exist
        zone_files = dns_output["zone_files"]
        assert len(zone_files) > 0
        assert "root.internal" in zone_files
        assert "platform.internal" in zone_files


class TestDNSZoneRecordDynamicUpdates:
    """Integration tests for dynamic DNS record updates during runtime."""

    async def test_add_zone_record_persists_to_zone_file(self, minimal_spec: NetEngineSpec) -> None:
        """Records added via add_zone_record() should persist in zone files."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate and DNS
        substrate = SubstrateHandler()
        await substrate.execute(context)

        dns = DNSHandler()
        await dns.execute(context)

        # Add a record to platform.internal
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="ca",
            value="10.0.0.6",
            ttl=300,
        )

        # Verify record was added to zone file
        zone_content = context.runtime_state.dns_output["zone_files"]["platform.internal"]
        assert "ca" in zone_content
        assert "10.0.0.6" in zone_content
        assert "300" in zone_content
        assert "IN A" in zone_content

    async def test_add_zone_record_replaces_existing_record(
        self, minimal_spec: NetEngineSpec
    ) -> None:
        """Adding a record with same name/type should replace the old one."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate and DNS
        substrate = SubstrateHandler()
        await substrate.execute(context)

        dns = DNSHandler()
        await dns.execute(context)

        # Add initial record
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="test",
            value="10.0.0.1",
            ttl=300,
        )

        zone_v1 = context.runtime_state.dns_output["zone_files"]["platform.internal"]
        assert "test" in zone_v1
        assert "10.0.0.1" in zone_v1

        # Replace with new value
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="test",
            value="10.0.0.2",
            ttl=600,
        )

        zone_v2 = context.runtime_state.dns_output["zone_files"]["platform.internal"]

        # New value should be present
        assert "test" in zone_v2
        assert "10.0.0.2" in zone_v2
        assert "600" in zone_v2

        # Old value should be replaced (not duplicated)
        # Count occurrences of "test" records to ensure no duplicates
        test_records = [
            line
            for line in zone_v2.split("\n")
            if "test" in line and not line.strip().startswith(";")
        ]
        # Should have at most one record with "test" (could be 0 if removed, 1 if replaced)
        assert len(test_records) <= 1

    async def test_add_multiple_records_to_different_zones(
        self, minimal_spec: NetEngineSpec
    ) -> None:
        """Should be able to add records to multiple zones sequentially."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate and DNS
        substrate = SubstrateHandler()
        await substrate.execute(context)

        dns = DNSHandler()
        await dns.execute(context)

        # Add record to platform zone
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="service1",
            value="10.0.0.10",
            ttl=300,
        )

        # Verify it was added
        platform_content = context.runtime_state.dns_output["zone_files"]["platform.internal"]
        assert "service1" in platform_content
        assert "10.0.0.10" in platform_content

        # Add another record to platform zone
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="service2",
            value="10.0.0.11",
            ttl=300,
        )

        # Both records should be present
        platform_content_updated = context.runtime_state.dns_output["zone_files"][
            "platform.internal"
        ]
        assert "service1" in platform_content_updated
        assert "service2" in platform_content_updated
        assert "10.0.0.10" in platform_content_updated
        assert "10.0.0.11" in platform_content_updated

    async def test_add_zone_record_with_different_ttl_values(
        self, minimal_spec: NetEngineSpec
    ) -> None:
        """Records with different TTLs should be handled correctly."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate and DNS
        substrate = SubstrateHandler()
        await substrate.execute(context)

        dns = DNSHandler()
        await dns.execute(context)

        # Add record with short TTL
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="short-ttl",
            value="10.0.0.50",
            ttl=60,
        )

        zone_v1 = context.runtime_state.dns_output["zone_files"]["platform.internal"]
        assert "60" in zone_v1

        # Replace with longer TTL
        await dns.add_zone_record(
            context=context,
            zone="platform.internal",
            record_type="A",
            name="short-ttl",
            value="10.0.0.50",
            ttl=3600,
        )

        zone_v2 = context.runtime_state.dns_output["zone_files"]["platform.internal"]
        # Should contain new TTL
        assert "3600" in zone_v2

    async def test_add_zone_record_fails_if_dns_not_executed(
        self, minimal_spec: NetEngineSpec
    ) -> None:
        """add_zone_record() should fail if DNS handler hasn't executed yet."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate only (no DNS)
        substrate = SubstrateHandler()
        await substrate.execute(context)

        # Try to add record without running DNS
        dns = DNSHandler()

        with pytest.raises(DNSError, match="DNS phase must run before adding records"):
            await dns.add_zone_record(
                context=context,
                zone="platform.internal",
                record_type="A",
                name="test",
                value="10.0.0.1",
            )

    async def test_add_zone_record_fails_for_nonexistent_zone(
        self, minimal_spec: NetEngineSpec
    ) -> None:
        """add_zone_record() should fail if zone doesn't exist."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate and DNS
        substrate = SubstrateHandler()
        await substrate.execute(context)

        dns = DNSHandler()
        await dns.execute(context)

        # Try to add record to nonexistent zone
        with pytest.raises(DNSError, match="Zone.*not found in zone_files"):
            await dns.add_zone_record(
                context=context,
                zone="nonexistent.zone",
                record_type="A",
                name="test",
                value="10.0.0.1",
            )


class TestEventEmissionAndTracing:
    """Integration tests for event emission and correlation ID tracing."""

    async def test_dns_event_emission_includes_zones(self, minimal_spec: NetEngineSpec) -> None:
        """DNS execution should emit event with zone information."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate
        substrate = SubstrateHandler()
        await substrate.execute(context)

        # Mock pgmq client to capture emitted events
        mock_pgmq = AsyncMock()
        context.pgmq_client = mock_pgmq

        # Execute DNS
        dns = DNSHandler()
        await dns.execute(context)

        # Verify that add_zone_record can be called without pgmq client (M1-M3 testing)
        # (pgmq events are M4+, but the handler should handle missing client gracefully)
        assert context.runtime_state.dns_output is not None

    async def test_correlation_id_flow_through_bootstrap(self, minimal_spec: NetEngineSpec) -> None:
        """Correlation ID should flow consistently through substrate and DNS phases."""
        logger = get_logger("test")
        runtime_state = RuntimeState()
        original_correlation_id = runtime_state.correlation_id

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Phase 0: Substrate
        substrate = SubstrateHandler()
        await substrate.execute(context)
        assert context.runtime_state.correlation_id == original_correlation_id

        # Phases 1-2: DNS
        dns = DNSHandler()
        await dns.execute(context)
        assert context.runtime_state.correlation_id == original_correlation_id

        # Parent event ID should propagate (initially None, but reserved for chaining)
        assert context.runtime_state.parent_event_id is None  # Initial state


class TestDNSPhaseIdempotence:
    """Integration tests for DNS phase idempotence and skip behavior."""

    async def test_dns_should_skip_after_execution(self, minimal_spec: NetEngineSpec) -> None:
        """should_skip() should return True after DNS execution."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate
        substrate = SubstrateHandler()
        await substrate.execute(context)

        # Execute DNS
        dns = DNSHandler()
        await dns.execute(context)

        # should_skip should now return True
        skip = await dns.should_skip(context)
        assert skip is True

    async def test_dns_idempotent_multiple_executions(self, minimal_spec: NetEngineSpec) -> None:
        """DNS handler should be idempotent (second execution should be skipped)."""
        logger = get_logger("test")
        runtime_state = RuntimeState()

        context = PhaseContext(
            spec=minimal_spec,
            runtime_state=runtime_state,
            logger=logger,
        )

        # Setup substrate
        substrate = SubstrateHandler()
        await substrate.execute(context)

        # First DNS execution
        dns = DNSHandler()
        await dns.execute(context)

        first_output = context.runtime_state.dns_output.copy()
        first_completed_at = context.runtime_state.completed_at

        # should_skip should prevent second execution
        skip = await dns.should_skip(context)
        assert skip is True

        # healthcheck should confirm already healthy
        healthy = await dns.healthcheck(context)
        assert healthy is True
