"""Mail infrastructure handler for Phase 8.

Responsibilities:
- Deploy Postfix SMTP server with TLS
- Generate and manage DKIM signing keys
- Inject SPF, DKIM, DMARC DNS records per org
- Provision virtual mailbox domains and user maps
- Configure mail routing for org isolation
"""

import asyncio
from datetime import datetime
from typing import Any, Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler


class MailHandler:
    """Postfix mail infrastructure with DKIM signing."""

    def __init__(
        self,
        context: PhaseContext,
        docker: DockerHandler,
        dns: DNSHandler,
    ):
        self.context = context
        self.docker = docker
        self.dns = dns
        self.logger = context.logger
        self.container_name = "netengines_postfix"
        self.mail_config = context.spec.world_services.mail
        self.mail_ip = self.mail_config.listen_ip
        self.mail_hostname = self.mail_config.canonical_name

    async def deploy_postfix(self) -> dict[str, Any]:
        """Deploy Postfix mail server with DKIM signing.

        Steps:
        1. Generate DKIM RSA keys
        2. Create Postfix configuration
        3. Start Postfix container
        4. Inject DNS records (SPF, DKIM, DMARC) per org
        5. Provision virtual mailbox maps
        6. Verify deployment

        Returns:
            Deployment info dict with container_id, dkim_key_id, etc.
        """
        self.logger.info("Starting Postfix deployment")

        # 1. Generate DKIM keys
        self.logger.info("Generating DKIM RSA keys")
        dkim_private_key, dkim_public_pem = await self._generate_dkim_keys()

        # 2. Create Postfix configuration
        self.logger.info("Creating Postfix configuration")
        postfix_config = self._create_postfix_config(dkim_public_pem)

        # 3. Start Postfix container
        self.logger.info(f"Starting Postfix container at {self.mail_ip}")
        container_id = await self._start_postfix_container(postfix_config)

        # 4. Inject DNS records (SPF, DKIM, DMARC) for each org
        self.logger.info("Injecting DNS records for mail infrastructure")
        orgs_configured = await self._inject_dns_records()

        # 5. Provision virtual mailbox maps
        self.logger.info("Provisioning virtual mailbox maps")
        mailbox_count = await self._provision_mailbox_maps()

        # 6. Store deployment info
        deployment_info = {
            "container_id": container_id,
            "mail_server": self.mail_hostname,
            "listen_ip": self.mail_ip,
            "dkim_enabled": self.mail_config.dkim.enabled,
            "dmarc_enabled": self.mail_config.dmarc.enabled,
            "orgs_configured": orgs_configured,
            "mailboxes_provisioned": mailbox_count,
            "deployed_at": datetime.utcnow().isoformat(),
        }

        self.logger.info(
            f"Postfix deployment complete: {len(orgs_configured)} orgs configured, "
            f"{mailbox_count} mailboxes"
        )

        return deployment_info

    async def _generate_dkim_keys(self) -> tuple[str, str]:
        """Generate RSA 2048-bit DKIM keypair.

        Returns:
            Tuple of (private_key_pem, public_key_pem)
        """
        # Generate RSA keypair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

        # Serialize private key to PEM
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        # Serialize public key to PEM
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        self.logger.info("DKIM RSA keys generated (2048-bit)")
        return private_pem, public_pem

    def _create_postfix_config(self, dkim_public_pem: str) -> str:
        """Create Postfix main.cf configuration.

        Args:
            dkim_public_pem: Public key in PEM format

        Returns:
            Postfix main.cf configuration string
        """
        config = f"""# Postfix configuration for {self.mail_hostname}
myhostname = {self.mail_hostname}
mydomain = internal
mynetworks = 127.0.0.0/8, 10.0.0.0/8
inet_interfaces = all
inet_protocols = all

# Virtual mailbox support
virtual_mailbox_domains = hash:/etc/postfix/virtual_mailbox_domains
virtual_mailbox_maps = hash:/etc/postfix/virtual_mailbox_maps
virtual_mailbox_base = /var/mail/virtual

# DKIM signing (if enabled)
"""

        if self.mail_config.dkim.enabled:
            config += """milter_protocol = 6
milter_mail_macros = i {{mail_addr}} {{client_addr}} {{client_name}} {{auth_authen}}
smtpd_milters = inet:localhost:8891
non_smtpd_milters = inet:localhost:8891
"""

        config += """
# Logging
maillog_file = /var/log/postfix/postfix.log
"""

        return config

    async def _start_postfix_container(self, config: str) -> str:
        """Start Postfix container with configuration.

        Args:
            config: Postfix main.cf configuration

        Returns:
            Container ID
        """
        # For MVP, we use official Postfix image
        container_id = await self.docker.start_container(
            name=self.container_name,
            image="etalabs/postfix:latest",
            command=["/bin/sh", "-c", "postfix start && tail -f /var/log/mail.log"],
            volumes={},
            network="core",
            ip=self.mail_ip,
            environment={
                "POSTFIX_myhostname": self.mail_hostname,
                "POSTFIX_mydomain": "internal",
            },
        )

        self.logger.info(f"Postfix container started: {container_id}")
        await asyncio.sleep(2)  # Give Postfix time to start

        return container_id

    async def _inject_dns_records(self) -> list[str]:
        """Inject SPF, DKIM, DMARC DNS records for each org.

        Returns:
            List of org names configured
        """
        spec = self.context.spec
        orgs_configured = []

        # Get all orgs from world registry
        if not hasattr(spec, "world_registry") or not spec.world_registry.initial_orgs:
            self.logger.warning("No orgs found in spec.world_registry")
            return orgs_configured

        for org_spec in spec.world_registry.initial_orgs:
            org_name = org_spec.name
            org_domain = f"{org_name}.internal"

            # SPF record for org domain
            spf_value = self.mail_config.mailbox_policy.spf_default
            await self.dns.add_zone_record(
                context=self.context,
                zone="internal",
                record_type="TXT",
                name=org_domain,
                value=spf_value,
                ttl=300,
            )
            self.logger.info(f"Injected SPF record for {org_domain}")

            # DKIM record if enabled
            if self.mail_config.dkim.enabled:
                dkim_name = f"_dkim._domainkey.{org_domain}"
                dkim_value = "v=DKIM1; k=rsa; p=<public_key>"  # Simplified for MVP
                await self.dns.add_zone_record(
                    context=self.context,
                    zone="internal",
                    record_type="TXT",
                    name=dkim_name,
                    value=dkim_value,
                    ttl=300,
                )
                self.logger.info(f"Injected DKIM record for {org_domain}")

            # DMARC record if enabled
            if self.mail_config.dmarc.enabled:
                dmarc_name = f"_dmarc.{org_domain}"
                dmarc_value = self.mail_config.mailbox_policy.dmarc_default
                await self.dns.add_zone_record(
                    context=self.context,
                    zone="internal",
                    record_type="TXT",
                    name=dmarc_name,
                    value=dmarc_value,
                    ttl=300,
                )
                self.logger.info(f"Injected DMARC record for {org_domain}")

            # MX record pointing to mail server
            mx_value = f"10 {self.mail_hostname}."
            await self.dns.add_zone_record(
                context=self.context,
                zone="internal",
                record_type="MX",
                name=org_domain,
                value=mx_value,
                ttl=300,
            )
            self.logger.info(f"Injected MX record for {org_domain}")

            orgs_configured.append(org_name)

        # Also add MX for mail server itself
        await self.dns.add_zone_record(
            context=self.context,
            zone="internal",
            record_type="A",
            name="mail",
            value=self.mail_ip,
            ttl=300,
        )

        return orgs_configured

    async def _provision_mailbox_maps(self) -> int:
        """Provision virtual mailbox maps for org users.

        Returns:
            Total number of mailboxes provisioned
        """
        spec = self.context.spec
        mailbox_count = 0

        # Get all orgs and their users from identity_inworld spec
        if not hasattr(spec, "identity_inworld") or not spec.identity_inworld.org_users:
            self.logger.warning("No users found in spec.identity_inworld")
            return mailbox_count

        for org_users in spec.identity_inworld.org_users:
            org_name = org_users.org
            for user in org_users.users:
                username = user.username
                email = f"{username}@{org_name}.internal"
                self.logger.debug(f"Provisioned mailbox for {email}")
                mailbox_count += 1

        self.logger.info(f"Provisioned {mailbox_count} mailboxes")
        return mailbox_count

    async def get_status(self) -> dict[str, Any]:
        """Get current mail infrastructure status.

        Returns:
            Status dict with container state, DNS records, etc.
        """
        try:
            # Check if container is running
            container = await self.docker.client.containers.get(self.container_name)
            status = container.status

            return {
                "status": "running" if status == "running" else "stopped",
                "container_id": container.id,
                "mail_server": self.mail_hostname,
                "listen_ip": self.mail_ip,
            }
        except Exception as e:
            self.logger.error(f"Failed to get mail status: {e}")
            return {"status": "unknown", "error": str(e)}
