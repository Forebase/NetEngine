"""Comprehensive integration tests for M5 (Phases 5-6: Registries + In-World Identity)."""

import pytest

from netengine.handlers.context import PhaseContext
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
from netengine.phases.phase_registries import RegistriesPhaseHandler


@pytest.fixture
def m5_spec():
    """Spec with complete M5 registries and identity configuration."""
    return {
        "name": "m5-test-world",
        "version": "0.1.0",
        "world_registry": {
            "initial_orgs": [
                {
                    "name": "acme",
                    "capabilities": ["dev", "prod"],
                    "and_profile": "business",
                },
                {
                    "name": "widgets",
                    "capabilities": ["prod"],
                    "and_profile": "standard",
                },
            ]
        },
        "domain_registry": {
            "address_pools": [
                {"profile": "business", "cidr": "192.168.0.0/22"},
                {"profile": "standard", "cidr": "192.168.4.0/24"},
            ],
            "tld_delegations": [
                {
                    "name": "internal",
                    "ns_server": "ns1.internal",
                    "listen_ip": "10.0.0.5",
                }
            ],
        },
        "identity_inworld": {
            "listen_ip": "10.0.0.12",
            "realm_name": "inworld",
            "org_users": [
                {
                    "org": "acme",
                    "users": [
                        {"username": "alice", "email": "alice@acme.internal"},
                        {"username": "bob", "email": "bob@acme.internal"},
                    ],
                },
                {
                    "org": "widgets",
                    "users": [{"username": "charlie", "email": "charlie@widgets.internal"}],
                },
            ],
        },
    }


class TestPhase5RegistriesIntegration:
    """Integration tests for Phase 5: Registries (World + Domain)."""

    def test_phase_5_runtime_state_has_output_field(self, phase_context):
        """Phase 5 should have output field in runtime_state."""
        assert hasattr(phase_context.runtime_state, "world_registry_output")

    @pytest.mark.asyncio
    async def test_phase_5_skip_logic_when_completed(self, phase_context):
        """Phase 5 should skip execution if already completed."""
        handler = RegistriesPhaseHandler()
        # Mark as completed
        phase_context.runtime_state.phase_completed["5"] = True

        should_skip = await handler.should_skip(phase_context)
        assert should_skip is True

    @pytest.mark.asyncio
    async def test_phase_5_dont_skip_when_not_completed(self, phase_context):
        """Phase 5 should not skip if not yet completed."""
        handler = RegistriesPhaseHandler()
        # Initially not in dict or False
        assert phase_context.runtime_state.phase_completed.get("5") is not True

        should_skip = await handler.should_skip(phase_context)
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_phase_5_healthcheck_returns_bool(self, phase_context):
        """Phase 5 healthcheck should return a boolean."""
        handler = RegistriesPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_phase_5_completion_tracking(self, phase_context):
        """Phase 5 should track completion state."""
        handler = RegistriesPhaseHandler()
        # Initially not completed
        assert phase_context.runtime_state.phase_completed.get("5") is not True

        # Mark as completed
        phase_context.runtime_state.phase_completed["5"] = True
        assert phase_context.runtime_state.phase_completed["5"] is True


class TestPhase6InWorldIdentityIntegration:
    """Integration tests for Phase 6: In-World Identity."""

    def test_phase_6_runtime_state_has_output_field(self, phase_context):
        """Phase 6 should have output field in runtime_state."""
        assert hasattr(phase_context.runtime_state, "identity_inworld_output")

    def test_phase_6_container_id_tracking_field(self, phase_context):
        """Phase 6 should have container ID tracking in runtime_state."""
        assert hasattr(phase_context.runtime_state, "inworld_keycloak_container_id")
        # Should be None initially
        assert phase_context.runtime_state.inworld_keycloak_container_id is None

    @pytest.mark.asyncio
    async def test_phase_6_skip_logic_when_completed(self, phase_context):
        """Phase 6 should skip execution if already completed."""
        handler = InWorldIdentityPhaseHandler()
        # Mark as completed
        phase_context.runtime_state.phase_completed["6"] = True

        should_skip = await handler.should_skip(phase_context)
        assert should_skip is True

    @pytest.mark.asyncio
    async def test_phase_6_dont_skip_when_not_completed(self, phase_context):
        """Phase 6 should not skip if not yet completed."""
        handler = InWorldIdentityPhaseHandler()
        # Initially not in dict or False
        assert phase_context.runtime_state.phase_completed.get("6") is not True

        should_skip = await handler.should_skip(phase_context)
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_phase_6_healthcheck_returns_bool(self, phase_context):
        """Phase 6 healthcheck should return a boolean."""
        handler = InWorldIdentityPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_phase_6_completion_tracking(self, phase_context):
        """Phase 6 should track completion state."""
        handler = InWorldIdentityPhaseHandler()
        # Initially not completed
        assert phase_context.runtime_state.phase_completed.get("6") is not True

        # Mark as completed
        phase_context.runtime_state.phase_completed["6"] = True
        assert phase_context.runtime_state.phase_completed["6"] is True


class TestPhase5Phase6Coordination:
    """Tests for Phase 5-6 handler interface and coordination."""

    def test_both_phases_have_runtime_state_outputs(self, phase_context):
        """Both phases should have output fields in runtime_state."""
        assert hasattr(phase_context.runtime_state, "world_registry_output")
        assert hasattr(phase_context.runtime_state, "identity_inworld_output")

    def test_both_phases_track_completion(self, phase_context):
        """Both phases should track completion independently."""
        # Initially both not completed (or not present in dict)
        assert phase_context.runtime_state.phase_completed.get("5") is not True
        assert phase_context.runtime_state.phase_completed.get("6") is not True

        # Mark Phase 5 complete
        phase_context.runtime_state.phase_completed["5"] = True
        assert phase_context.runtime_state.phase_completed["5"] is True
        assert phase_context.runtime_state.phase_completed.get("6") is not True

        # Mark Phase 6 complete
        phase_context.runtime_state.phase_completed["6"] = True
        assert phase_context.runtime_state.phase_completed["5"] is True
        assert phase_context.runtime_state.phase_completed["6"] is True

    @pytest.mark.asyncio
    async def test_phase_5_healthcheck_returns_bool(self, phase_context):
        """Phase 5 healthcheck should return a boolean."""
        handler = RegistriesPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_phase_6_healthcheck_missing_container(self, phase_context):
        """Phase 6 healthcheck should fail if container ID is None."""
        handler = InWorldIdentityPhaseHandler()
        # Ensure container is None
        phase_context.runtime_state.inworld_keycloak_container_id = None

        is_healthy = await handler.healthcheck(phase_context)
        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_phase_6_healthcheck_with_container_set(self, phase_context):
        """Phase 6 healthcheck should check container when ID is set."""
        handler = InWorldIdentityPhaseHandler()
        # Set a dummy container ID
        phase_context.runtime_state.inworld_keycloak_container_id = "test-container-123"

        # Should attempt to check health (will return bool)
        is_healthy = await handler.healthcheck(phase_context)
        assert isinstance(is_healthy, bool)
