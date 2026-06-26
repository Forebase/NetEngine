"""Integration tests for Phase 5: World Registry + Domain Registry + WHOIS.

Covers execute() behaviour with mocked infrastructure — the interface contract
(should_skip / healthcheck) is already exercised in test_m4_phases.py and
test_m5_registries.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.phases.phase_registries import RegistriesPhaseHandler


@pytest.fixture
def phase_context_with_supervisor(phase_context):
    """Phase context with a mock ConsumerSupervisor for Phase 5 tests."""
    phase_context.consumer_supervisor = MagicMock()
    phase_context.consumer_supervisor.register = MagicMock()
    return phase_context


@pytest.fixture
def patched_registry_deps(phase_context_with_supervisor):
    """Phase context with all Phase 5 external dependencies mocked out."""
    mock_world = MagicMock()
    mock_world.seed_from_spec = AsyncMock()

    mock_domain = MagicMock()
    mock_domain.seed_address_pools = AsyncMock()

    mock_whois = MagicMock()
    mock_whois.start = AsyncMock()

    mock_dns = MagicMock()
    mock_dns.add_zone_record = AsyncMock()

    patches = [
        patch(
            "netengine.phases.phase_registries.WorldRegistryHandler",
            MagicMock(return_value=mock_world),
        ),
        patch(
            "netengine.phases.phase_registries.DomainRegistryHandler",
            MagicMock(return_value=mock_domain),
        ),
        patch(
            "netengine.phases.phase_registries.WHOISServer",
            MagicMock(return_value=mock_whois),
        ),
        patch(
            "netengine.phases.phase_registries.DNSHandler",
            MagicMock(return_value=mock_dns),
        ),
    ]

    for p in patches:
        p.start()

    yield {
        "context": phase_context_with_supervisor,
        "world": mock_world,
        "domain": mock_domain,
        "whois": mock_whois,
        "dns": mock_dns,
    }

    for p in patches:
        p.stop()


class TestRegistriesPhaseHandlerExecute:
    """Tests for Phase 5 execute() with mocked external dependencies."""

    @pytest.mark.asyncio
    async def test_execute_populates_world_registry_output(self, patched_registry_deps):
        """Phase 5 execute should set world_registry_output on runtime_state."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        assert ctx.runtime_state.world_registry_output is not None

    @pytest.mark.asyncio
    async def test_execute_populates_domain_registry_output(self, patched_registry_deps):
        """Phase 5 execute should set domain_registry_output on runtime_state."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        assert ctx.runtime_state.domain_registry_output is not None

    @pytest.mark.asyncio
    async def test_execute_world_registry_output_has_required_keys(self, patched_registry_deps):
        """Phase 5 world_registry_output must include seeded flag and deployed_at."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        output = ctx.runtime_state.world_registry_output
        assert "seeded" in output
        assert "deployed_at" in output
        assert output["seeded"] is True

    @pytest.mark.asyncio
    async def test_execute_domain_registry_output_has_required_keys(self, patched_registry_deps):
        """Phase 5 domain_registry_output must include tld_delegations and deployed_at."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        output = ctx.runtime_state.domain_registry_output
        assert "address_pools_seeded" in output
        assert "tld_delegations" in output
        assert "deployed_at" in output

    @pytest.mark.asyncio
    async def test_execute_marks_phase_completed(self, patched_registry_deps):
        """Phase 5 execute should set phase_completed['5'] = True."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        assert ctx.runtime_state.phase_completed.get("5") is True

    @pytest.mark.asyncio
    async def test_execute_seeds_world_registry_from_spec(self, patched_registry_deps):
        """Phase 5 should call WorldRegistryHandler.seed_from_spec with the spec."""
        ctx = patched_registry_deps["context"]
        mock_world = patched_registry_deps["world"]
        await RegistriesPhaseHandler().execute(ctx)

        mock_world.seed_from_spec.assert_awaited_once_with(ctx.spec)

    @pytest.mark.asyncio
    async def test_execute_seeds_domain_address_pools(self, patched_registry_deps):
        """Phase 5 should call DomainRegistryHandler.seed_address_pools with the spec."""
        ctx = patched_registry_deps["context"]
        mock_domain = patched_registry_deps["domain"]
        await RegistriesPhaseHandler().execute(ctx)

        mock_domain.seed_address_pools.assert_awaited_once_with(ctx.spec)

    @pytest.mark.asyncio
    async def test_execute_registers_whois_server_with_supervisor(self, patched_registry_deps):
        """Phase 5 should register the WHOIS server task with consumer_supervisor."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        registered_names = [
            call.args[0] for call in ctx.consumer_supervisor.register.call_args_list
        ]
        assert "whois_server" in registered_names

    @pytest.mark.asyncio
    async def test_execute_registers_dns_updates_consumer(self, patched_registry_deps):
        """Phase 5 should register the dns_updates consumer with consumer_supervisor."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        registered_names = [
            call.args[0] for call in ctx.consumer_supervisor.register.call_args_list
        ]
        assert "dns_updates" in registered_names

    @pytest.mark.asyncio
    async def test_execute_creates_ns_and_a_records_for_each_tld(self, patched_registry_deps):
        """Phase 5 should emit NS + A zone records for every configured TLD."""
        ctx = patched_registry_deps["context"]
        mock_dns = patched_registry_deps["dns"]
        await RegistriesPhaseHandler().execute(ctx)

        # minimal.yaml has one TLD ('internal', listen_ip 10.0.0.4) → 2 DNS calls
        assert mock_dns.add_zone_record.await_count == 2

        calls = mock_dns.add_zone_record.await_args_list
        ns_call = calls[0]
        assert ns_call.kwargs["record_type"] == "NS"
        assert ns_call.kwargs["name"] == "internal"
        assert ns_call.kwargs["value"] == "ns.internal"

        a_call = calls[1]
        assert a_call.kwargs["record_type"] == "A"
        assert a_call.kwargs["name"] == "ns.internal"
        assert a_call.kwargs["value"] == "10.0.0.4"

    @pytest.mark.asyncio
    async def test_execute_tld_delegations_recorded_in_output(self, patched_registry_deps):
        """Phase 5 domain_registry_output should include TLD delegation data from spec."""
        ctx = patched_registry_deps["context"]
        await RegistriesPhaseHandler().execute(ctx)

        delegations = ctx.runtime_state.domain_registry_output["tld_delegations"]
        assert isinstance(delegations, list)
        # minimal.yaml defines one TLD: internal
        assert len(delegations) == 1
        assert delegations[0]["name"] == "internal"
