"""Integration tests for Phase 6: In-World Identity.

Tests cover:
- Per-org Keycloak realm creation
- User provisioning from spec
- OIDC client credential storage
- Event-driven org admission provisioning
- Health checks and idempotence
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from netengine.handlers.context import PhaseContext
from netengine.handlers._base import BasePhaseHandler
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
from netengine.spec.models import (
    NetEngineSpec,
    IdentityInWorldPhase,
    OrgUsers,
    InWorldUser,
)
from netengine.core.state import RuntimeState
from netengine.logging import get_logger


class TestM6InWorldIdentityInterfaceCompliance:
    """Tests that M6 handler implements BasePhaseHandler contract."""

    def test_m6_is_phase_handler(self) -> None:
        """InWorldIdentityPhaseHandler must implement BasePhaseHandler."""
        assert issubclass(InWorldIdentityPhaseHandler, BasePhaseHandler)

    async def test_m6_has_execute_method(self) -> None:
        """Handler must have execute method."""
        handler = InWorldIdentityPhaseHandler()
        assert hasattr(handler, "execute")
        assert callable(handler.execute)

    async def test_m6_has_healthcheck_method(self) -> None:
        """Handler must have healthcheck method."""
        handler = InWorldIdentityPhaseHandler()
        assert hasattr(handler, "healthcheck")
        assert callable(handler.healthcheck)

    async def test_m6_has_should_skip_method(self) -> None:
        """Handler must have should_skip method."""
        handler = InWorldIdentityPhaseHandler()
        assert hasattr(handler, "should_skip")
        assert callable(handler.should_skip)


class TestM6PrerequisiteValidation:
    """Tests that M6 validates M1-M5 prerequisites."""

    async def test_m6_fails_without_substrate(self) -> None:
        """M6 should fail if substrate_output is None."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = None  # Not run
        runtime_state.dns_output = {"root_zone": {}}  # Exists

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="Substrate phase.*must complete"):
            await handler.execute(phase_context)

    async def test_m6_fails_without_dns(self) -> None:
        """M6 should fail if dns_output is None."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}  # Exists
        runtime_state.dns_output = None  # Not run

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="DNS phase.*must complete"):
            await handler.execute(phase_context)


class TestM6RealmCreationPerOrg:
    """Tests that M6 creates one realm per organization."""

    async def test_m6_creates_realm_for_each_org(self) -> None:
        """M6 should create one realm per org in spec."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}

        # Create spec with two orgs
        user1 = InWorldUser(username="alice", email="alice@acme.com")
        user2 = InWorldUser(username="bob", email="bob@acme.com")
        org_users_list = [
            OrgUsers(org="acme-corp", users=[user1, user2]),
            OrgUsers(org="widgets-inc", users=[user1]),
        ]

        inworld_spec = IdentityInWorldPhase(
            canonical_name="auth.internal",
            listen_ip="10.0.0.12",
            org_users=org_users_list,
        )

        spec = MagicMock()
        spec.identity_inworld = inworld_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock Keycloak operations
        with patch.object(
            handler, "_start_keycloak_container", new_callable=AsyncMock
        ) as mock_start:
            with patch.object(handler, "_create_org_client", new_callable=AsyncMock):
                with patch(
                    "netengine.phases.phase_inworld_identity.OIDCHandler"
                ) as mock_oidc_class:
                    mock_oidc = AsyncMock()
                    mock_oidc.create_platform_realm = AsyncMock()
                    mock_oidc_class.return_value = mock_oidc

                    mock_start.return_value = "container-123"

                    await handler.execute(phase_context)

        # Verify realms_created contains both orgs
        output = runtime_state.identity_inworld_output
        assert output is not None
        assert "realms_created" in output
        assert "acme-corp-realm" in output["realms_created"]
        assert "widgets-inc-realm" in output["realms_created"]


class TestM6UserProvisioning:
    """Tests that M6 seeds users from spec."""

    async def test_m6_seeds_users_from_spec(self) -> None:
        """M6 should create users defined in spec.org_users."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}

        user1 = InWorldUser(username="alice", email="alice@acme.com")
        user2 = InWorldUser(username="bob", email="bob@acme.com")

        inworld_spec = IdentityInWorldPhase(
            canonical_name="auth.internal",
            org_users=[OrgUsers(org="acme-corp", users=[user1, user2])],
        )

        spec = MagicMock()
        spec.identity_inworld = inworld_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        with patch.object(
            handler, "_start_keycloak_container", new_callable=AsyncMock
        ) as mock_start:
            with patch.object(handler, "_create_org_client", new_callable=AsyncMock):
                with patch(
                    "netengine.phases.phase_inworld_identity.OIDCHandler"
                ) as mock_oidc_class:
                    mock_oidc = AsyncMock()
                    mock_oidc.create_platform_realm = AsyncMock()
                    mock_oidc.create_user = AsyncMock()
                    mock_oidc_class.return_value = mock_oidc

                    mock_start.return_value = "container-123"

                    await handler.execute(phase_context)

        # Verify create_user was called for each user
        assert mock_oidc.create_user.call_count >= 2
        call_args_list = mock_oidc.create_user.call_args_list
        usernames = [call.kwargs.get("username") for call in call_args_list]
        assert "alice" in usernames
        assert "bob" in usernames


class TestM6Healthcheck:
    """Tests that M6 healthcheck verifies Keycloak is running."""

    async def test_m6_healthcheck_fails_without_output(self) -> None:
        """Healthcheck should fail if identity_inworld_output is None."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.identity_inworld_output = None

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.healthcheck(phase_context)
        assert result is False

    async def test_m6_healthcheck_requires_container_id(self) -> None:
        """Healthcheck should fail if container_id missing from output."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.identity_inworld_output = {
            "realms_created": ["acme-corp-realm"],
            # Missing keycloak_container_id
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.healthcheck(phase_context)
        assert result is False

    async def test_m6_healthcheck_requires_realms(self) -> None:
        """Healthcheck should fail if no realms created."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.identity_inworld_output = {
            "keycloak_container_id": "container-123",
            "realms_created": [],  # Empty
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.healthcheck(phase_context)
        assert result is False


class TestM6Idempotence:
    """Tests that M6 is idempotent (skips if already deployed)."""

    async def test_m6_should_skip_if_already_deployed(self) -> None:
        """should_skip should return True if identity_inworld_output exists."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.identity_inworld_output = {
            "keycloak_container_id": "container-123",
            "realms_created": ["acme-corp-realm"],
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.should_skip(phase_context)
        assert result is True

    async def test_m6_should_execute_if_not_deployed(self) -> None:
        """should_skip should return False if not yet deployed."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.identity_inworld_output = None

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.should_skip(phase_context)
        assert result is False


class TestM6OrgAdmissionEvents:
    """Tests that M6 consumes org.admitted events for dynamic provisioning."""

    async def test_m6_processes_org_admitted_event(self) -> None:
        """M6 should create realm/client when org.admitted event received."""
        handler = InWorldIdentityPhaseHandler()

        # Mock OIDC handler
        mock_oidc = AsyncMock()
        mock_oidc.create_platform_realm = AsyncMock()

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
                    "emitted_at": "2026-06-21T12:00:00",
                    "payload": {"org_name": "new-org"},
                    "retry_count": 0,
                }
            ),
        }

        # First call returns the org.admitted event, second returns None (exit loop after 1 event)
        mock_pgmq.receive = AsyncMock(side_effect=[mock_msg, None])
        mock_pgmq.delete = AsyncMock()

        phase_context = MagicMock()
        phase_context.pgmq_client = mock_pgmq
        phase_context.logger = get_logger("test")

        inworld_spec = IdentityInWorldPhase(canonical_name="auth.internal")

        # Run consumer but exit after first event
        with patch.object(handler, "_create_org_client", new_callable=AsyncMock):
            # Create a task with timeout to prevent infinite loop
            import asyncio

            consumer_task = asyncio.create_task(
                handler._consume_org_admission_events(phase_context, mock_oidc, inworld_spec)
            )

            # Give it time to process
            await asyncio.sleep(0.1)
            consumer_task.cancel()

            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

        # Verify event was processed
        mock_pgmq.delete.assert_called()


class TestM6EventEmission:
    """Tests that M6 emits proper events."""

    async def test_m6_emits_inworld_identity_ready_event(self) -> None:
        """M6 should emit inworld_identity.ready event on success."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.correlation_id = "test-correlation-123"
        runtime_state.parent_event_id = None

        inworld_spec = IdentityInWorldPhase(
            canonical_name="auth.internal",
            org_users=[OrgUsers(org="acme-corp", users=[])],
        )

        spec = MagicMock()
        spec.identity_inworld = inworld_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        with patch.object(
            handler, "_start_keycloak_container", new_callable=AsyncMock
        ) as mock_start:
            with patch.object(handler, "_create_org_client", new_callable=AsyncMock):
                with patch(
                    "netengine.phases.phase_inworld_identity.OIDCHandler"
                ) as mock_oidc_class:
                    mock_oidc = AsyncMock()
                    mock_oidc.create_platform_realm = AsyncMock()
                    mock_oidc_class.return_value = mock_oidc

                    mock_start.return_value = "container-123"

                    with patch.object(handler, "_emit_event", new_callable=AsyncMock) as mock_emit:
                        await handler.execute(phase_context)

        # Verify event was emitted
        mock_emit.assert_called()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["event_type"] == "inworld_identity.ready"
        assert "realms_created" in call_kwargs["payload"]


class TestM6OutputStructure:
    """Tests that M6 produces correct output structure."""

    async def test_m6_output_contains_required_fields(self) -> None:
        """M6 should populate all required fields in identity_inworld_output."""
        handler = InWorldIdentityPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}

        inworld_spec = IdentityInWorldPhase(
            canonical_name="auth.internal",
            org_users=[OrgUsers(org="acme-corp", users=[])],
        )

        spec = MagicMock()
        spec.identity_inworld = inworld_spec

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        with patch.object(
            handler, "_start_keycloak_container", new_callable=AsyncMock
        ) as mock_start:
            with patch.object(handler, "_create_org_client", new_callable=AsyncMock):
                with patch(
                    "netengine.phases.phase_inworld_identity.OIDCHandler"
                ) as mock_oidc_class:
                    mock_oidc = AsyncMock()
                    mock_oidc.create_platform_realm = AsyncMock()
                    mock_oidc_class.return_value = mock_oidc

                    mock_start.return_value = "container-123"

                    await handler.execute(phase_context)

        output = runtime_state.identity_inworld_output
        assert output is not None
        assert "keycloak_container_id" in output
        assert "realms_created" in output
        assert "credentials_stored" in output
        assert "deployed_at" in output
        assert output["keycloak_container_id"] == "container-123"
        assert output["realms_created"] == ["acme-corp-realm"]
        assert output["credentials_stored"] >= 1
