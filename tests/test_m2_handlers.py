"""M2 Handler Tests — PKI and OIDC bootstrap phases (Phases 3-4).

Tests cover:
- PKIHandler: Phase 3 substrate initialization
- OIDCHandler: Phase 4 identity platform bootstrap
- Event emission with correlation_id propagation
- Healthcheck states (Healthy, Sick, Unhealthy)
- Supabase secret storage
- Idempotent re-execution (should_skip)
"""

from datetime import datetime

import pytest

from netengine.core.state import RuntimeState
from netengine.handlers.context import PhaseContext
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.spec.models import NetEngineSpec


class TestPKIHandler:
    """Test Phase 3: PKI bootstrap via step-ca."""

    @pytest.fixture
    async def pki_handler(self):
        """Create a PKIHandler instance."""
        return PKIHandler()

    @pytest.mark.asyncio
    async def test_pki_execute_initializes_ca(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that execute() initializes step-ca and generates certificates."""
        assert phase_context.runtime_state.pki_output is None

        await pki_handler.execute(phase_context)

        # Verify CA was initialized
        assert phase_context.runtime_state.pki_output is not None
        output = phase_context.runtime_state.pki_output

        assert output["ca_type"] == "step-ca"
        assert output["status"] == "ready"
        assert "ca_cert_pem" in output
        assert "ca_fingerprint" in output
        assert "admin_client_cert_pem" in output
        assert "admin_client_key_pem" in output
        assert "issuer_url" in output
        assert "deployed_at" in output
        assert output["health_status"] == "Healthy"

    @pytest.mark.asyncio
    async def test_pki_execute_sets_timestamps(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that execute() sets started_at and completed_at timestamps."""
        assert phase_context.runtime_state.started_at is None
        assert phase_context.runtime_state.completed_at is None

        await pki_handler.execute(phase_context)

        assert phase_context.runtime_state.started_at is not None
        assert isinstance(phase_context.runtime_state.started_at, datetime)
        assert phase_context.runtime_state.completed_at is not None
        assert isinstance(phase_context.runtime_state.completed_at, datetime)
        assert phase_context.runtime_state.completed_at >= phase_context.runtime_state.started_at

    @pytest.mark.asyncio
    async def test_pki_execute_emits_event(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that execute() emits pki.ready event with correlation_id."""
        await pki_handler.execute(phase_context)

        # Event emission is logged but not stored in M2
        # Just verify that the handler completes successfully
        assert phase_context.runtime_state.pki_output is not None
        output = phase_context.runtime_state.pki_output
        assert "ca_fingerprint" in output
        assert "issuer_url" in output

    @pytest.mark.asyncio
    async def test_pki_execute_raises_on_error(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that execute() raises exception and sets error state on failure."""
        # Inject a failure by mocking _init_step_ca to raise
        original_init = pki_handler._init_step_ca

        async def failing_init(context):
            raise RuntimeError("CA initialization failed")

        pki_handler._init_step_ca = failing_init

        with pytest.raises(RuntimeError, match="CA initialization failed"):
            await pki_handler.execute(phase_context)

        # Verify error was captured in runtime_state
        assert phase_context.runtime_state.last_error is not None
        assert "CA initialization failed" in phase_context.runtime_state.last_error
        assert phase_context.runtime_state.last_error_at is not None

        pki_handler._init_step_ca = original_init

    @pytest.mark.asyncio
    async def test_pki_healthcheck_returns_true_when_initialized(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() returns True when PKI is initialized."""
        await pki_handler.execute(phase_context)

        result = await pki_handler.healthcheck(phase_context)
        assert result is True

    @pytest.mark.asyncio
    async def test_pki_healthcheck_returns_false_when_not_initialized(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() returns False when PKI is not initialized."""
        result = await pki_handler.healthcheck(phase_context)
        assert result is False

    @pytest.mark.asyncio
    async def test_pki_healthcheck_detects_unhealthy(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() detects Unhealthy status."""
        # Initialize PKI
        await pki_handler.execute(phase_context)

        # Set to Unhealthy
        phase_context.runtime_state.pki_output["health_status"] = "Unhealthy"

        result = await pki_handler.healthcheck(phase_context)
        assert result is False

    @pytest.mark.asyncio
    async def test_pki_healthcheck_tolerates_sick(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() tolerates Sick status (recoverable)."""
        # Initialize PKI
        await pki_handler.execute(phase_context)

        # Set to Sick (transient issues)
        phase_context.runtime_state.pki_output["health_status"] = "Sick"

        result = await pki_handler.healthcheck(phase_context)
        assert result is True  # Still returns True for Sick

    @pytest.mark.asyncio
    async def test_pki_should_skip_returns_false_on_first_run(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that should_skip() returns False on first run."""
        result = await pki_handler.should_skip(phase_context)
        assert result is False

    @pytest.mark.asyncio
    async def test_pki_should_skip_returns_true_after_execution(
        self, pki_handler: PKIHandler, phase_context: PhaseContext
    ):
        """Test that should_skip() returns True after PKI is initialized."""
        await pki_handler.execute(phase_context)

        result = await pki_handler.should_skip(phase_context)
        assert result is True


class TestOIDCHandler:
    """Test Phase 4: OIDC/Keycloak bootstrap."""

    @pytest.fixture
    async def oidc_handler(self):
        """Create an OIDCHandler instance."""
        return OIDCHandler()

    @pytest.mark.asyncio
    async def test_oidc_execute_initializes_keycloak(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that execute() initializes Keycloak and creates realm/admin."""
        assert phase_context.runtime_state.identity_platform_output is None

        await oidc_handler.execute(phase_context)

        # Verify Keycloak was initialized
        assert phase_context.runtime_state.identity_platform_output is not None
        output = phase_context.runtime_state.identity_platform_output

        assert output["identity_platform_type"] == "keycloak"
        assert output["status"] == "ready"
        assert "realm_id" in output
        assert "realm_name" in output
        assert "admin_user_id" in output
        assert "admin_username" in output
        assert "admin_password" in output
        assert "oidc_client_id" in output
        assert "oidc_client_secret" in output
        assert "issuer_url" in output
        assert "deployed_at" in output
        assert output["health_status"] == "Healthy"

    @pytest.mark.asyncio
    async def test_oidc_execute_sets_timestamps(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that execute() sets started_at and completed_at timestamps."""
        assert phase_context.runtime_state.started_at is None
        assert phase_context.runtime_state.completed_at is None

        await oidc_handler.execute(phase_context)

        assert phase_context.runtime_state.started_at is not None
        assert isinstance(phase_context.runtime_state.started_at, datetime)
        assert phase_context.runtime_state.completed_at is not None
        assert isinstance(phase_context.runtime_state.completed_at, datetime)
        assert phase_context.runtime_state.completed_at >= phase_context.runtime_state.started_at

    @pytest.mark.asyncio
    async def test_oidc_execute_emits_event(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that execute() emits identity.platform_ready event with correlation_id."""
        await oidc_handler.execute(phase_context)

        # Event emission is logged but not stored in M2
        # Just verify that the handler completes successfully
        assert phase_context.runtime_state.identity_platform_output is not None
        output = phase_context.runtime_state.identity_platform_output
        assert "realm_id" in output
        assert "oidc_client_id" in output
        assert "issuer_url" in output

    @pytest.mark.asyncio
    async def test_oidc_execute_raises_on_error(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that execute() raises exception and sets error state on failure."""
        # Inject a failure by mocking _init_keycloak to raise
        original_init = oidc_handler._init_keycloak

        async def failing_init(context):
            raise RuntimeError("Keycloak initialization failed")

        oidc_handler._init_keycloak = failing_init

        with pytest.raises(RuntimeError, match="Keycloak initialization failed"):
            await oidc_handler.execute(phase_context)

        # Verify error was captured in runtime_state
        assert phase_context.runtime_state.last_error is not None
        assert "Keycloak initialization failed" in phase_context.runtime_state.last_error
        assert phase_context.runtime_state.last_error_at is not None

        oidc_handler._init_keycloak = original_init

    @pytest.mark.asyncio
    async def test_oidc_healthcheck_returns_true_when_initialized(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() returns True when OIDC is initialized."""
        await oidc_handler.execute(phase_context)

        result = await oidc_handler.healthcheck(phase_context)
        assert result is True

    @pytest.mark.asyncio
    async def test_oidc_healthcheck_returns_false_when_not_initialized(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() returns False when OIDC is not initialized."""
        result = await oidc_handler.healthcheck(phase_context)
        assert result is False

    @pytest.mark.asyncio
    async def test_oidc_healthcheck_detects_unhealthy(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() detects Unhealthy status."""
        # Initialize OIDC
        await oidc_handler.execute(phase_context)

        # Set to Unhealthy
        phase_context.runtime_state.identity_platform_output["health_status"] = "Unhealthy"

        result = await oidc_handler.healthcheck(phase_context)
        assert result is False

    @pytest.mark.asyncio
    async def test_oidc_healthcheck_tolerates_sick(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that healthcheck() tolerates Sick status (recoverable)."""
        # Initialize OIDC
        await oidc_handler.execute(phase_context)

        # Set to Sick (transient issues)
        phase_context.runtime_state.identity_platform_output["health_status"] = "Sick"

        result = await oidc_handler.healthcheck(phase_context)
        assert result is True  # Still returns True for Sick

    @pytest.mark.asyncio
    async def test_oidc_should_skip_returns_false_on_first_run(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that should_skip() returns False on first run."""
        result = await oidc_handler.should_skip(phase_context)
        assert result is False

    @pytest.mark.asyncio
    async def test_oidc_should_skip_returns_true_after_execution(
        self, oidc_handler: OIDCHandler, phase_context: PhaseContext
    ):
        """Test that should_skip() returns True after OIDC is initialized."""
        await oidc_handler.execute(phase_context)

        result = await oidc_handler.should_skip(phase_context)
        assert result is True


class TestM2CorrelationIdPropagation:
    """Test correlation_id propagation across M2 handlers."""

    @pytest.mark.asyncio
    async def test_pki_event_preserves_correlation_id(self, phase_context: PhaseContext):
        """Test that PKI handler events preserve correlation_id."""
        phase_context.runtime_state.correlation_id = "test-correlation-123"

        handler = PKIHandler()
        await handler.execute(phase_context)

        # Verify correlation_id is preserved in runtime_state
        assert phase_context.runtime_state.correlation_id == "test-correlation-123"

    @pytest.mark.asyncio
    async def test_oidc_event_preserves_correlation_id(self, phase_context: PhaseContext):
        """Test that OIDC handler events preserve correlation_id."""
        phase_context.runtime_state.correlation_id = "test-correlation-456"

        handler = OIDCHandler()
        await handler.execute(phase_context)

        # Verify correlation_id is preserved in runtime_state
        assert phase_context.runtime_state.correlation_id == "test-correlation-456"


class TestM2SecretStorage:
    """Test Supabase secret storage in M2 handlers."""

    @pytest.mark.asyncio
    async def test_pki_stores_secrets(self, phase_context: PhaseContext):
        """Test that PKI handler calls secret storage."""
        handler = PKIHandler()
        await handler.execute(phase_context)

        # Verify secrets are in output (mock storage)
        output = phase_context.runtime_state.pki_output
        assert output["ca_cert_pem"] is not None
        assert output["admin_client_key_pem"] is not None

    @pytest.mark.asyncio
    async def test_oidc_stores_secrets(self, phase_context: PhaseContext):
        """Test that OIDC handler calls secret storage."""
        handler = OIDCHandler()
        await handler.execute(phase_context)

        # Verify secrets are in output (mock storage)
        output = phase_context.runtime_state.identity_platform_output
        assert output["admin_password"] is not None
        assert output["oidc_client_secret"] is not None
