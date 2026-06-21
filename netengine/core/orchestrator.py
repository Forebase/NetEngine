import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict

from netengine.core.state import RuntimeState
from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler

logger = logging.getLogger(__name__)

phase_handlers = [
    DNSHandler(),  # phases 1-2
    PKIPhaseHandler(),  # phase 3
    InWorldIdentityPhaseHandler(),  # phase 4
    # ... more phases later
]


@dataclass
class PhaseContext:
    state: RuntimeState
    docker: DockerHandler
    dns: DNSHandler
    # Other handlers will be added later
    spec: Dict[str, Any]  # loaded YAML spec


class Orchestrator:
    def __init__(self, spec: Dict[str, Any]):
        self.spec = spec
        self.state = RuntimeState.load()
        self.docker = DockerHandler()
        self.dns = DNSHandler(self.docker, self.state)
        self.context = PhaseContext(state=self.state, docker=self.docker, dns=self.dns, spec=spec)
        self.phases = [
            self.phase_0_substrate,
            self.phase_1_dns_root,
            self.phase_2_dns_hierarchy,
            self.phase_3_pki,  # M2
            self.phase_5_registries,
            self.phase_6_inworld_identity,
            self.phase_7_ands,
            self.phase_8_services,
        ]

    async def run(self):
        for i, phase_func in enumerate(self.phases):
            phase_name = phase_func.__name__
            if self.state.phase_completed.get(str(i), False):
                logger.info(f"Phase {i} ({phase_name}) already completed, skipping.")
                continue
            logger.info(f"Running Phase {i}: {phase_name}")
            try:
                await phase_func()
                self.state.phase_completed[str(i)] = True
                self.state.save()
                logger.info(f"Phase {i} completed successfully.")
            except Exception as e:
                logger.error(f"Phase {i} failed: {e}")
                raise

    # --- Phase 0: Substrate (stub, assume already implemented) ---
    async def phase_0_substrate(self):
        # M0 already built, so we just ensure networks exist.
        # For demonstration, we'll create the core network if missing.
        # In real code, this would use the spec to create networks.
        pass

    # --- Phase 1: DNS Root (stub) ---
    async def phase_1_dns_root(self):
        # Already implemented in M1.
        pass

    # --- Phase 2: DNS Hierarchy (stub) ---
    async def phase_2_dns_hierarchy(self):
        # Already implemented in M1.
        pass

    # --- Phase 3: PKI + ACME (M2) ---
    async def phase_3_pki(self):
        pki = PKIHandler(self.docker, self.state)
        await pki.bootstrap()
        # Register DNS record for ca.platform.internal
        await self.dns.add_zone_record(
            zone="platform.internal", record_type="A", name="ca", value=pki.ca_ip, ttl=300
        )
        # Optionally, ensure platform zone exists
        await self.dns.ensure_zone(
            "platform.internal", "ns1.platform.internal.", "ns1.platform.internal."
        )


async def phase_4_platform_identity(context):
    # 1. Ensure Supabase migrations run (idempotent)
    from netengine.utils.run_migrations import apply_migrations

    await apply_migrations(context.supabase)

    # 2. Start Keycloak container
    from netengine.handlers.oidc_handler import OIDCHandler

    oidc = OIDCHandler(context.state, context.supabase)

    # Get cert from PKI handler for auth.platform.internal
    pki = PKIHandler(context.docker, context.state)
    cert, key = await pki.issue_cert(common_name="auth.platform.internal", sans=[])

    # Start container (use docker_handler)
    await context.docker.start_container(
        name="netengine_keycloak_platform",
        image="quay.io/keycloak/keycloak:23.0.7",
        command=["start"],
        volumes={...},  # mount cert/key
        network="core",
        ip="10.0.0.7",
        environment={
            "KC_HOSTNAME": "auth.platform.internal",
            "KC_HTTPS_CERTIFICATE_FILE": "/certs/tls.crt",
            "KC_HTTPS_CERTIFICATE_KEY_FILE": "/certs/tls.key",
            "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
            "KC_BOOTSTRAP_ADMIN_PASSWORD": context.bootstrap_admin_password,
        },
    )

    # 3. Healthcheck
    # wait for /health/ready

    # 4. Register DNS
    await context.dns.add_zone_record("platform.internal", "A", "auth", "10.0.0.7", 300)

    # 5. Bootstrap realm (via Admin API)
    # Need to get an admin token. Use the bootstrap admin credentials.
    await oidc.create_platform_realm()

    # 6. Create OIDC scopes (if needed)
    # 7. Update state
    context.state.phase_completed["4"] = True
    await context.state.save()


async def phase_3_pki(context):
    pki = PKIHandler(context.docker, context.state)
    # 1. Generate CA (if not already generated)
    if not context.state.ca_cert_pem:
        await pki.generate_root_ca()
    # 2. Start step-ca server
    await pki.start_ca_server()
    # 3. Healthcheck
    if not await pki.healthcheck():
        raise RuntimeError("step-ca not responding")
    # 4. Register DNS record for ca.platform.internal
    dns = DNSHandler(context.docker, context.state)
    await dns.add_zone_record(
        zone="platform.internal", record_type="A", name="ca", value=pki.ca_ip, ttl=300
    )
    # 5. Update state
    context.state.phase_completed["3"] = True
    await context.state.save()
