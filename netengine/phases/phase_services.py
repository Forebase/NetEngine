import asyncio
from datetime import datetime
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.dns import DNSHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.handlers.oidc_handler import OIDCHandler
from netengine.handlers.mail_handler import MailHandler
from netengine.handlers.minio_handler import StorageHandler
from netengine.handlers.app_handler import AppHandler

class ServicesPhaseHandler(BasePhaseHandler):
    """Phase 8: World services + org app deployment."""

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec

        docker = DockerHandler()
        dns = DNSHandler()
        pki = PKIHandler(docker, context.runtime_state, spec)
        oidc = OIDCHandler(
            keycloak_url="https://auth.internal",
            admin_username="admin",
            admin_password=context.runtime_state.inworld_admin_password
        )

        # 1. Deploy world services
        mail = MailHandler(docker, dns, context.runtime_state)
        if spec.get("world_services", {}).get("mail", {}).get("enabled", False):
            logger.info("Deploying Mailpit")
            await mail.deploy_mailpit()

        storage = StorageHandler(docker, dns, pki, context.runtime_state)
        if spec.get("world_services", {}).get("storage", {}).get("enabled", False):
            logger.info("Deploying MinIO")
            await storage.deploy_minio()

        # 2. Deploy org apps from spec
        app_handler = AppHandler(docker, dns, pki, oidc, context.runtime_state)
        deployments = spec.get("org_apps", {}).get("deployments", [])
        for deployment in deployments:
            org = deployment["org"]
            app_name = deployment["app"]
            subdomain = deployment.get("subdomain", app_name)
            config = deployment.get("config", {})
            logger.info(f"Deploying {app_name} for {org} at {subdomain}.{org}.internal")
            await app_handler.deploy_app(org, app_name, subdomain, config)

        # 3. Set up pgmq consumer for app deployments triggered by org admission
        # (optional – we can also trigger deployment on org admission)
        asyncio.create_task(self._consume_org_admissions(context))

        context.runtime_state.phase_completed["8"] = True
        context.runtime_state.save()
        logger.info("Phase 8 complete: world services and org apps ready")

    async def _consume_org_admissions(self, context):
        """Optionally auto‑deploy a default app when a new org is admitted."""
        pass