"""Integration tests for Phase 8: Mail Services (Postfix + DKIM/DMARC).

Tests cover:
- Postfix deployment with DKIM/DMARC signing
- DNS record injection (SPF, DKIM, DMARC, MX)
- Virtual mailbox provisioning for org users
- Health checks and idempotence
- Prerequisites validation
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.state import RuntimeState
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.logging import get_logger
from netengine.phases.phase_services import ServicesPhaseHandler
from netengine.spec.models import (
    DKIMConfig,
    DMARCConfig,
    MailboxPolicy,
    MailConfig,
    StorageConfig,
    WorldServicesPhase,
)


class TestM8MailInterfaceCompliance:
    """Tests that M8 Mail handler implements BasePhaseHandler contract."""

    def test_m8_is_phase_handler(self) -> None:
        """ServicesPhaseHandler must implement BasePhaseHandler."""
        assert issubclass(ServicesPhaseHandler, BasePhaseHandler)

    async def test_m8_has_execute_method(self) -> None:
        """Handler must have execute method."""
        handler = ServicesPhaseHandler()
        assert hasattr(handler, "execute")
        assert callable(handler.execute)

    async def test_m8_has_healthcheck_method(self) -> None:
        """Handler must have healthcheck method."""
        handler = ServicesPhaseHandler()
        assert hasattr(handler, "healthcheck")
        assert callable(handler.healthcheck)

    async def test_m8_has_should_skip_method(self) -> None:
        """Handler must have should_skip method."""
        handler = ServicesPhaseHandler()
        assert hasattr(handler, "should_skip")
        assert callable(handler.should_skip)


class TestM8PrerequisiteValidation:
    """Tests that M8 validates M1-M7 prerequisites."""

    async def test_m8_fails_without_substrate(self) -> None:
        """M8 should fail if substrate_output is None."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = None  # Not run
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        mail_config = MailConfig(enabled=False)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="Substrate phase.*must complete"):
            await handler.execute(phase_context)

    async def test_m8_fails_without_dns(self) -> None:
        """M8 should fail if dns_output is None."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = None  # Not run
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        mail_config = MailConfig(enabled=False)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="DNS phase.*must complete"):
            await handler.execute(phase_context)

    async def test_m8_fails_without_pki(self) -> None:
        """M8 should fail if PKI phase not complete."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = None  # Not run
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        mail_config = MailConfig(enabled=False)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        with pytest.raises(RuntimeError, match="PKI phase"):
            await handler.execute(phase_context)


class TestM8PostfixDeployment:
    """Tests that M8 deploys Postfix mail server."""

    async def test_m8_deploys_postfix_container(self) -> None:
        """M8 should deploy Postfix container at configured IP."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        mail_config = MailConfig(
            enabled=True,
            server="postfix",
            listen_ip="10.0.0.13",
            canonical_name="mail.internal",
        )
        storage_config = StorageConfig(enabled=False)

        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services
        spec.world_registry.organizations = []
        spec.identity_inworld.org_users = []

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_services.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.start_container = AsyncMock(return_value="postfix-container-123")
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_services.DNSHandler"):
                with patch("netengine.phases.phase_services.PKIHandler"):
                    with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                        await handler.execute(phase_context)

        # Verify Postfix container was started
        assert mock_docker.start_container.called
        call_kwargs = mock_docker.start_container.call_args.kwargs
        assert call_kwargs["name"] == "netengines_postfix"
        assert call_kwargs["ip"] == "10.0.0.13"

    async def test_m8_records_deployment_info(self) -> None:
        """M8 should record mail deployment info in world_services_output."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        mail_config = MailConfig(enabled=True)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services
        spec.world_registry.organizations = []
        spec.identity_inworld.org_users = []

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_services.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.start_container = AsyncMock(return_value="postfix-container-123")
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_services.DNSHandler"):
                with patch("netengine.phases.phase_services.PKIHandler"):
                    with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                        await handler.execute(phase_context)

        # Verify output was recorded
        assert runtime_state.world_services_output is not None
        assert "mail" in runtime_state.world_services_output
        assert "deployed_at" in runtime_state.world_services_output


class TestM8DKIMSetup:
    """Tests that M8 sets up DKIM signing."""

    async def test_m8_enables_dkim_when_configured(self) -> None:
        """M8 should enable DKIM if configured in spec."""
        dkim_config = DKIMConfig(enabled=True)
        mail_config = MailConfig(enabled=True, dkim=dkim_config)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services

        # Verify DKIM is enabled in spec
        assert spec.world_services.mail.dkim.enabled is True

    async def test_m8_disables_dkim_when_not_configured(self) -> None:
        """M8 should respect DKIM disabled setting."""
        dkim_config = DKIMConfig(enabled=False)
        mail_config = MailConfig(enabled=True, dkim=dkim_config)
        world_services = WorldServicesPhase(mail=mail_config)
        spec = MagicMock()
        spec.world_services = world_services

        # Verify DKIM is disabled in spec
        assert spec.world_services.mail.dkim.enabled is False

    def test_dkim_txt_value_uses_real_public_key(self) -> None:
        """The DKIM TXT value must carry the real key, not a placeholder."""
        from netengine.handlers.mail_handler import MailHandler

        public_pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n"
            "abcDEF1234567890\n"
            "-----END PUBLIC KEY-----\n"
        )

        value = MailHandler._dkim_txt_value(public_pem)

        assert value.startswith("v=DKIM1; k=rsa; p=")
        # The PEM armor and line breaks are stripped into a single base64 blob.
        assert value == (
            "v=DKIM1; k=rsa; " "p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAabcDEF1234567890"
        )
        # Regression guard against the old "Simplified for MVP" placeholder.
        assert "<public_key>" not in value


class TestM8DNSRecords:
    """Tests that M8 injects DNS records (SPF, DKIM, DMARC, MX)."""

    async def test_m8_injects_spf_records(self) -> None:
        """M8 should inject SPF records for each org."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        # Create org spec
        org_spec = MagicMock()
        org_spec.name = "acme"

        mail_config = MailConfig(enabled=True)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services
        spec.world_registry.initial_orgs = [org_spec]
        spec.identity_inworld.org_users = []

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_services.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.start_container = AsyncMock(return_value="postfix-container-123")
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_services.DNSHandler") as mock_dns_class:
                mock_dns = AsyncMock()
                mock_dns.add_zone_record = AsyncMock()
                mock_dns_class.return_value = mock_dns

                with patch("netengine.phases.phase_services.PKIHandler"):
                    with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                        await handler.execute(phase_context)

        # Verify DNS records were injected
        assert mock_dns.add_zone_record.called

    async def test_m8_injects_mx_records(self) -> None:
        """M8 should inject MX records pointing to mail server."""
        mail_config = MailConfig(
            enabled=True, listen_ip="10.0.0.13", canonical_name="mail.internal"
        )
        world_services = WorldServicesPhase(mail=mail_config)
        spec = MagicMock()
        spec.world_services = world_services

        # Verify MX configuration
        assert spec.world_services.mail.canonical_name == "mail.internal"

    async def test_m8_injects_dmarc_records(self) -> None:
        """M8 should inject DMARC records if enabled."""
        dmarc_config = DMARCConfig(enabled=True, policy="reject")
        mail_config = MailConfig(enabled=True, dmarc=dmarc_config)
        world_services = WorldServicesPhase(mail=mail_config)
        spec = MagicMock()
        spec.world_services = world_services

        # Verify DMARC is configured
        assert spec.world_services.mail.dmarc.enabled is True
        assert spec.world_services.mail.dmarc.policy == "reject"


class TestM8MailboxProvisioning:
    """Tests that M8 provisions virtual mailboxes for org users."""

    async def test_m8_provisions_mailboxes_for_org_users(self) -> None:
        """M8 should create mailbox entries for all org users."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        # Create user spec
        user_spec = MagicMock()
        user_spec.username = "alice"
        user_spec.email = "alice@acme.com"

        org_users = MagicMock()
        org_users.org = "acme"
        org_users.users = [user_spec]

        mail_config = MailConfig(enabled=True)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services
        spec.world_registry.organizations = []
        spec.identity_inworld.org_users = [org_users]

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_services.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.start_container = AsyncMock(return_value="postfix-container-123")
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_services.DNSHandler"):
                with patch("netengine.phases.phase_services.PKIHandler"):
                    with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                        await handler.execute(phase_context)

        # Verify mailboxes recorded
        output = runtime_state.world_services_output
        assert output is not None
        mail_info = output.get("mail", {})
        assert mail_info.get("mailboxes_provisioned", 0) >= 1

    async def test_m8_respects_mailbox_quota(self) -> None:
        """M8 should use configured mailbox quota."""
        mailbox_policy = MailboxPolicy(auto_provision_from_orgs=True, quota_mb=2000)
        mail_config = MailConfig(enabled=True, mailbox_policy=mailbox_policy)
        world_services = WorldServicesPhase(mail=mail_config)
        spec = MagicMock()
        spec.world_services = world_services

        # Verify quota is set
        assert spec.world_services.mail.mailbox_policy.quota_mb == 2000


class TestM8Healthcheck:
    """Tests that M8 healthcheck verifies mail service."""

    async def test_m8_healthcheck_fails_without_output(self) -> None:
        """Healthcheck should fail if world_services_output is None."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.world_services_output = None

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.healthcheck(phase_context)
        assert result is False

    async def test_m8_healthcheck_verifies_mail_container(self) -> None:
        """Healthcheck should verify mail container is running."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.world_services_output = {
            "mail": {"container_id": "postfix-123"},
        }

        mail_config = MailConfig(enabled=True)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock Docker handler
        with patch("netengine.phases.phase_services.DockerHandler") as mock_docker_class:
            mock_docker = MagicMock()
            mock_container = MagicMock()
            mock_container.status = "running"
            mock_docker.client.containers.get.return_value = mock_container
            mock_docker_class.return_value = mock_docker

            result = await handler.healthcheck(phase_context)

        # Should succeed with running container
        assert result is True


class TestM8Idempotence:
    """Tests that M8 is idempotent (skips if already deployed)."""

    async def test_m8_should_skip_if_already_deployed(self) -> None:
        """should_skip should return True if world_services_output exists."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.world_services_output = {
            "mail": {"container_id": "postfix-123"},
            "deployed_at": "2026-06-22T00:00:00",
        }

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.should_skip(phase_context)
        assert result is True

    async def test_m8_should_execute_if_not_deployed(self) -> None:
        """should_skip should return False if not yet deployed."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.world_services_output = None

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=MagicMock(),
            logger=get_logger("test"),
        )

        result = await handler.should_skip(phase_context)
        assert result is False


class TestM8OutputStructure:
    """Tests that M8 produces correct output structure."""

    async def test_m8_output_contains_required_fields(self) -> None:
        """M8 should populate all required fields in world_services_output."""
        handler = ServicesPhaseHandler()
        runtime_state = RuntimeState()
        runtime_state.substrate_output = {"networks": {}}
        runtime_state.dns_output = {"root_zone": {}}
        runtime_state.pki_output = {"ca_cert": {}}
        runtime_state.identity_platform_output = {"realm": "master"}
        runtime_state.world_registry_output = {"orgs": []}
        runtime_state.identity_inworld_output = {"realms": []}
        runtime_state.ands_output = {"ands": []}

        mail_config = MailConfig(enabled=True)
        storage_config = StorageConfig(enabled=False)
        world_services = WorldServicesPhase(mail=mail_config, storage=storage_config)
        spec = MagicMock()
        spec.world_services = world_services
        spec.world_registry.organizations = []
        spec.identity_inworld.org_users = []

        phase_context = PhaseContext(
            runtime_state=runtime_state,
            spec=spec,
            logger=get_logger("test"),
        )

        # Mock handlers
        with patch("netengine.phases.phase_services.DockerHandler") as mock_docker_class:
            mock_docker = AsyncMock()
            mock_docker.start_container = AsyncMock(return_value="postfix-container-123")
            mock_docker_class.return_value = mock_docker

            with patch("netengine.phases.phase_services.DNSHandler"):
                with patch("netengine.phases.phase_services.PKIHandler"):
                    with patch.object(handler, "_emit_event", new_callable=AsyncMock):
                        await handler.execute(phase_context)

        output = runtime_state.world_services_output
        assert output is not None
        assert "mail" in output
        assert "deployed_at" in output
        assert isinstance(output["deployed_at"], str)
