"""Integration tests for M4 phases (Registries + In-world Identity)."""

import pytest

from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
from netengine.phases.phase_registries import RegistriesPhaseHandler


@pytest.fixture
def m4_spec():
    """Spec with M4 registries and identity configuration."""
    return {
        "name": "m4-test-world",
        "version": "0.1.0",
        "world_registry": {
            "initial_orgs": [
                {"name": "acme", "capabilities": ["dev", "prod"], "and_profile": "business"},
            ]
        },
        "domain_registry": {
            "address_pools": [{"profile": "business", "cidr": "192.168.0.0/22"}],
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
                        {
                            "username": "alice",
                            "email": "alice@acme.internal",
                        }
                    ],
                }
            ],
        },
    }


class TestPhase5RegistriesHandler:
    """Tests for Phase 5: Registries handler contract."""

    def test_phase_5_implements_base_interface(self):
        """Phase 5 should implement BasePhaseHandler interface."""
        handler = RegistriesPhaseHandler()
        assert hasattr(handler, "execute")
        assert hasattr(handler, "healthcheck")
        assert hasattr(handler, "should_skip")

    @pytest.mark.asyncio
    async def test_phase_5_should_skip_if_completed(self, phase_context):
        """Phase 5 should skip if already completed."""
        handler = RegistriesPhaseHandler()
        phase_context.runtime_state.phase_completed["5"] = True

        skip = await handler.should_skip(phase_context)
        assert skip is True

    @pytest.mark.asyncio
    async def test_phase_5_should_not_skip_if_not_completed(self, phase_context):
        """Phase 5 should not skip if not yet completed."""
        handler = RegistriesPhaseHandler()
        phase_context.runtime_state.phase_completed["5"] = False

        skip = await handler.should_skip(phase_context)
        assert skip is False

    @pytest.mark.asyncio
    async def test_phase_5_healthcheck_returns_bool(self, phase_context):
        """Phase 5 healthcheck should return a boolean."""
        handler = RegistriesPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)


class TestPhase6InWorldIdentityHandler:
    """Tests for Phase 6: In-world Identity handler contract."""

    def test_phase_6_implements_base_interface(self):
        """Phase 6 should implement BasePhaseHandler interface."""
        handler = InWorldIdentityPhaseHandler()
        assert hasattr(handler, "execute")
        assert hasattr(handler, "healthcheck")
        assert hasattr(handler, "should_skip")

    @pytest.mark.asyncio
    async def test_phase_6_should_skip_if_completed(self, phase_context):
        """Phase 6 should skip if already completed."""
        handler = InWorldIdentityPhaseHandler()
        phase_context.runtime_state.phase_completed["6"] = True

        skip = await handler.should_skip(phase_context)
        assert skip is True

    @pytest.mark.asyncio
    async def test_phase_6_should_not_skip_if_not_completed(self, phase_context):
        """Phase 6 should not skip if not yet completed."""
        handler = InWorldIdentityPhaseHandler()
        phase_context.runtime_state.phase_completed["6"] = False

        skip = await handler.should_skip(phase_context)
        assert skip is False

    @pytest.mark.asyncio
    async def test_phase_6_healthcheck_fails_without_container(self, phase_context):
        """Phase 6 healthcheck should fail if container not started."""
        handler = InWorldIdentityPhaseHandler()
        # No container ID set
        phase_context.runtime_state.inworld_keycloak_container_id = None

        is_healthy = await handler.healthcheck(phase_context)
        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_phase_6_healthcheck_returns_bool(self, phase_context):
        """Phase 6 healthcheck should return a boolean."""
        handler = InWorldIdentityPhaseHandler()
        result = await handler.healthcheck(phase_context)
        assert isinstance(result, bool)
