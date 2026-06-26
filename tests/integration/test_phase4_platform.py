"""Integration tests for Phase 4: Platform Identity (Keycloak + Supabase).

Covers execute() behaviour with mocked infrastructure — the interface contract
(should_skip / healthcheck) is already exercised in test_m3_bootstrap.py.
"""

from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler


@pytest.fixture
def patched_platform_deps(phase_context):
    """Phase context with all Phase 4 external dependencies mocked out."""
    mock_pki = MagicMock()
    mock_pki.issue_cert = AsyncMock(return_value=("CERT-DATA", "KEY-DATA"))

    mock_docker = MagicMock()
    mock_docker.start_container = AsyncMock(return_value="keycloak-ctr-abc")

    mock_oidc = MagicMock()
    mock_oidc.create_platform_realm = AsyncMock(return_value="platform-realm-id")
    mock_oidc.create_admin_user = AsyncMock(return_value="admin-user-id")
    mock_oidc.create_client = AsyncMock(return_value="platform-api-client-id")
    mock_oidc.add_token_mapper = AsyncMock()

    mock_dns = MagicMock()
    mock_dns.add_zone_record = AsyncMock()

    patches = [
        patch("netengine.phases.phase_platform_identity.apply_migrations", AsyncMock()),
        patch(
            "netengine.phases.phase_platform_identity.PKIHandler",
            MagicMock(return_value=mock_pki),
        ),
        patch(
            "netengine.phases.phase_platform_identity.DockerHandler",
            MagicMock(return_value=mock_docker),
        ),
        patch(
            "netengine.phases.phase_platform_identity.OIDCHandler",
            MagicMock(return_value=mock_oidc),
        ),
        patch(
            "netengine.phases.phase_platform_identity.DNSHandler",
            MagicMock(return_value=mock_dns),
        ),
        patch("netengine.phases.phase_platform_identity.os"),
        patch("netengine.phases.phase_platform_identity.open", mock_open()),
        patch.object(PlatformIdentityPhaseHandler, "_wait_for_keycloak", AsyncMock()),
    ]

    for p in patches:
        p.start()

    yield {
        "context": phase_context,
        "pki": mock_pki,
        "docker": mock_docker,
        "oidc": mock_oidc,
        "dns": mock_dns,
    }

    for p in patches:
        p.stop()


class TestPlatformIdentityPhaseHandlerExecute:
    """Tests for Phase 4 execute() with mocked external dependencies."""

    @pytest.mark.asyncio
    async def test_execute_populates_identity_platform_output(self, patched_platform_deps):
        """Phase 4 execute should set identity_platform_output on runtime_state."""
        ctx = patched_platform_deps["context"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        assert ctx.runtime_state.identity_platform_output is not None

    @pytest.mark.asyncio
    async def test_execute_output_has_required_keys(self, patched_platform_deps):
        """Phase 4 output must include all required identity_platform_output keys."""
        ctx = patched_platform_deps["context"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        output = ctx.runtime_state.identity_platform_output
        for key in (
            "keycloak_container_id",
            "platform_realm_id",
            "admin_user_id",
            "platform_client_id",
            "deployed_at",
        ):
            assert key in output, f"Missing required key '{key}' in identity_platform_output"

    @pytest.mark.asyncio
    async def test_execute_marks_phase_completed(self, patched_platform_deps):
        """Phase 4 execute should set phase_completed['4'] = True."""
        ctx = patched_platform_deps["context"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        assert ctx.runtime_state.phase_completed.get("4") is True

    @pytest.mark.asyncio
    async def test_execute_sets_container_id_on_runtime_state(self, patched_platform_deps):
        """Phase 4 should persist the Keycloak container ID to runtime_state."""
        ctx = patched_platform_deps["context"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        assert ctx.runtime_state.keycloak_platform_container_id == "keycloak-ctr-abc"

    @pytest.mark.asyncio
    async def test_execute_registers_auth_dns_record(self, patched_platform_deps):
        """Phase 4 should register an A record for auth.<zone> pointing to the listen IP."""
        ctx = patched_platform_deps["context"]
        mock_dns = patched_platform_deps["dns"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        # spec.identity_platform.listen_ip == "10.0.0.7" in minimal.yaml
        mock_dns.add_zone_record.assert_called_once_with(
            ctx, "platform.internal", "A", "auth", "10.0.0.7", 300
        )

    @pytest.mark.asyncio
    async def test_execute_generates_bootstrap_admin_password(self, patched_platform_deps):
        """Phase 4 should generate a bootstrap admin password when none exists."""
        ctx = patched_platform_deps["context"]
        assert ctx.runtime_state.bootstrap_admin_password is None

        await PlatformIdentityPhaseHandler().execute(ctx)

        assert ctx.runtime_state.bootstrap_admin_password is not None
        assert len(ctx.runtime_state.bootstrap_admin_password) > 0

    @pytest.mark.asyncio
    async def test_execute_reuses_existing_admin_password(self, patched_platform_deps):
        """Phase 4 should not overwrite a bootstrap admin password already in state."""
        ctx = patched_platform_deps["context"]
        ctx.runtime_state.bootstrap_admin_password = "already-set-password"

        await PlatformIdentityPhaseHandler().execute(ctx)

        assert ctx.runtime_state.bootstrap_admin_password == "already-set-password"

    @pytest.mark.asyncio
    async def test_execute_issues_tls_cert_for_auth_hostname(self, patched_platform_deps):
        """Phase 4 should request a TLS cert for auth.platform.internal via PKIHandler."""
        ctx = patched_platform_deps["context"]
        mock_pki = patched_platform_deps["pki"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        mock_pki.issue_cert.assert_awaited_once_with("auth.platform.internal", [])

    @pytest.mark.asyncio
    async def test_execute_creates_platform_realm(self, patched_platform_deps):
        """Phase 4 should create the platform OIDC realm via OIDCHandler."""
        ctx = patched_platform_deps["context"]
        mock_oidc = patched_platform_deps["oidc"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        mock_oidc.create_platform_realm.assert_awaited_once_with("platform")

    @pytest.mark.asyncio
    async def test_execute_persists_realm_and_user_ids(self, patched_platform_deps):
        """Phase 4 should write realm and user IDs to runtime_state."""
        ctx = patched_platform_deps["context"]
        await PlatformIdentityPhaseHandler().execute(ctx)

        assert ctx.runtime_state.platform_realm_id == "platform-realm-id"
        assert ctx.runtime_state.admin_user_id == "admin-user-id"
        assert ctx.runtime_state.platform_client_id == "platform-api-client-id"
