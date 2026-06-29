"""Phase 7: Administrative Network Domains (ANDs).

Responsibilities:
- Create per-org ANDs for network isolation
- Allocate subnets from address pools per AND profile
- Generate and apply nftables rules on gateway
- Register DNS zones for AND suffixes
- Event-driven provisioning for new organizations
- Support AND profile changes (rule updates)
"""

import asyncio
import ipaddress
import json
from datetime import UTC, datetime
from typing import Any, Optional

from netengine.events.queues import Queue
from netengine.events.emitter import emit_event
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.gateway_handler import GatewayHandler


class ANDsPhaseHandler(BasePhaseHandler):
    """Phase 7: Administrative Network Domains.

    Creates isolated network domains per organization with:
    - Subnet allocation from address pools (M5 pre-created)
    - Docker bridge networks for isolation
    - nftables rules on gateway for policy enforcement
    - DNS zone delegation for AND suffix
    - Event-driven provisioning for new orgs

    Design:
    - One AND per org (N:1 org:AND for MVP)
    - Address pools created by M5 (this phase just allocates)
    - Strict AND profile validation against spec
    - RuntimeState + Supabase dual-tracking for state
    - Configurable healthcheck depth (light/medium/deep)
    """

    async def execute(self, context: PhaseContext) -> None:
        logger = context.logger
        spec = context.spec
        ands_spec = spec.ands

        logger.info("Starting Phase 7: AND provisioning")

        # Validate prerequisites
        if context.runtime_state.substrate_output is None:
            raise RuntimeError(
                "Substrate phase (Phase 0) must complete before ANDs. "
                "Ensure Phase 0 has run and created networks."
            )
        if context.runtime_state.dns_output is None:
            raise RuntimeError(
                "DNS phase (Phase 1-2) must complete before ANDs. "
                "Ensure Phase 1-2 have run and created zones."
            )
        if context.runtime_state.domain_registry_output is None:
            raise RuntimeError(
                "Domain Registry (Phase 5b) must complete before ANDs. "
                "Ensure address pools are created."
            )

        context.runtime_state.started_at = datetime.now(UTC)

        try:
            ands_output: dict[str, Any] = {}

            # profiles is dict[str, ANDProfileDef]
            available_profiles: set[str] = (
                set(ands_spec.profiles.keys()) if ands_spec.profiles else set()
            )
            if not available_profiles:
                logger.warning("No AND profiles defined in spec; using default 'business' profile")
                available_profiles = {"business"}

            # Validate each AND instance references a known profile
            for and_instance in ands_spec.instances:
                if and_instance.profile not in available_profiles:
                    raise RuntimeError(
                        f"AND {and_instance.name} references undefined profile "
                        f"'{and_instance.profile}'. Available: {available_profiles}"
                    )

            # Initialize gateway handler
            docker = DockerHandler()
            gateway = GatewayHandler(docker)

            # Provision each AND
            ands_provisioned = []
            address_allocations = {}
            profiles_used = set()

            for and_instance in ands_spec.instances:
                logger.info(
                    f"Provisioning AND: {and_instance.name} for {and_instance.org} "
                    f"with profile {and_instance.profile}"
                )

                and_data = await self._provision_and(
                    context,
                    docker,
                    gateway,
                    and_instance,
                    ands_spec,
                )

                ands_provisioned.append(and_instance.name)
                address_allocations[and_instance.name] = and_data["cidr"]
                profiles_used.add(and_instance.profile)

                # Store in runtime_state for this session
                if not hasattr(context.runtime_state, "ands_instances"):
                    context.runtime_state.ands_instances = {}  # type: ignore[attr-defined]
                context.runtime_state.ands_instances[and_instance.name] = and_data  # type: ignore[attr-defined]

            ands_output["ands_provisioned"] = ands_provisioned
            ands_output["address_allocations"] = address_allocations
            ands_output["profiles_used"] = list(profiles_used)
            ands_output["deployed_at"] = datetime.now(UTC).isoformat()

            context.runtime_state.ands_output = ands_output
            context.runtime_state.completed_at = datetime.now(UTC)

            logger.info(f"Phase 7 complete: {len(ands_provisioned)} ANDs provisioned")

            await self._emit_event(
                context,
                event_type="ands.ready",
                payload={
                    "ands_provisioned": ands_provisioned,
                    "profiles_used": list(profiles_used),
                },
            )

            # Register org-admission consumer through ConsumerSupervisor so
            # crashes are visible and the task is gracefully shut down.
            if context.pgmq_client is not None:
                context.consumer_supervisor.register(  # type: ignore[union-attr]
                    "org_admission_events",
                    lambda: self._consume_org_admission_events(context, docker, gateway, ands_spec),
                )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.now(UTC)
            logger.error(f"Phase 7 setup failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        logger = context.logger

        try:
            if context.runtime_state.ands_output is None:
                logger.warning("ANDs not yet initialized")
                return False

            output = context.runtime_state.ands_output
            ands_provisioned = output.get("ands_provisioned", [])

            if not ands_provisioned:
                logger.warning("No ANDs provisioned")
                return False

            if not hasattr(context.runtime_state, "ands_instances"):
                logger.warning("AND instances not tracked in runtime_state")
                return False

            instances = context.runtime_state.ands_instances  # type: ignore[attr-defined]
            if not all(and_name in instances for and_name in ands_provisioned):
                logger.warning("Some AND instances missing from runtime_state")
                return False

            # Medium check: verify Docker networks exist
            docker = DockerHandler()
            for and_name in ands_provisioned:
                bridge_name = f"netengines_and_{and_name}"
                try:
                    network = docker.client.networks.get(bridge_name)
                    if not network:
                        logger.warning(f"AND network missing: {bridge_name}")
                        return False
                except Exception as e:
                    logger.warning(f"Failed to check AND network {bridge_name}: {e}")
                    return False

            logger.info("ANDs healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"ANDs healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        if context.runtime_state.ands_output is not None:
            context.logger.info("ANDs already provisioned, skipping Phase 7")
            return True
        return False

    # ─────────────────────────────────────────────
    # AND Provisioning
    # ─────────────────────────────────────────────

    async def _provision_and(
        self,
        context: PhaseContext,
        docker: DockerHandler,
        gateway: GatewayHandler,
        and_instance: Any,
        ands_spec: Any,
    ) -> dict[str, Any]:
        logger = context.logger

        # 1. Resolve profile object (profiles is dict[str, ANDProfileDef])
        profile_name: str = and_instance.profile
        profile_obj = ands_spec.profiles.get(profile_name) if ands_spec.profiles else None
        if profile_obj is None:
            raise RuntimeError(f"Profile '{profile_name}' not found in spec")

        # 2. Allocate subnet
        cidr = await self._allocate_address(and_instance.name, profile_name, ands_spec)
        logger.info(f"Allocated CIDR for {and_instance.name}: {cidr}")

        # 3. Create isolated Docker bridge network
        bridge_name = f"netengines_and_{and_instance.name}"
        try:
            await docker.create_network(
                name=bridge_name,
                driver="bridge",
                subnet=cidr,
                internal=True,
            )
            logger.info(f"Created Docker bridge: {bridge_name}")
        except Exception as e:
            raise RuntimeError(f"Failed to create network {bridge_name}: {e}")

        # 4. Attach gateway to this AND network
        network = ipaddress.ip_network(cidr)
        gateway_ip = str(network.network_address + 1)

        try:
            await docker.connect_network(
                container=gateway.gateway_container,
                network=bridge_name,
                ip=gateway_ip,
            )
            logger.info(f"Attached gateway to {bridge_name} at {gateway_ip}")
        except Exception as e:
            await docker.remove_network(bridge_name)
            raise RuntimeError(f"Failed to attach gateway to {bridge_name}: {e}")

        # 5. Generate and apply nftables rules
        try:
            rules = await gateway.generate_rules(
                and_name=and_instance.name,
                profile=profile_name,
                cidr=cidr,
            )
            await gateway.apply_rules(and_name=and_instance.name, rules=rules)
            logger.info(f"Applied nftables rules for {and_instance.name}")
        except Exception as e:
            # Clean up on failure
            await docker.disconnect_network(gateway.gateway_container, bridge_name)
            await docker.remove_network(bridge_name)
            raise RuntimeError(f"Failed to apply rules for {and_instance.name}: {e}")

        # 6. Register DNS zone
        dns_suffix = and_instance.dns_suffix or f"{and_instance.org}.internal"
        try:
            from netengine.handlers.dns import DNSHandler

            dns = DNSHandler()
            await dns.add_zone_record(
                context=context,
                zone="internal",
                record_type="A",
                name=dns_suffix.rstrip("."),
                value=gateway_ip,
                ttl=300,
            )
            logger.info(f"Registered DNS for {dns_suffix} -> {gateway_ip}")
        except Exception as e:
            logger.warning(f"Failed to register DNS for {dns_suffix}: {e}")

        # 7. Store state
        and_data = {
            "name": and_instance.name,
            "org": and_instance.org,
            "profile": profile_name,
            "cidr": cidr,
            "gateway_ip": gateway_ip,
            "bridge_name": bridge_name,
            "dns_suffix": dns_suffix,
            "deployed_at": datetime.now(UTC).isoformat(),
        }

        try:
            supabase = await self._get_supabase()
            await supabase.table("and_instances").upsert(and_data).execute()
            logger.info(f"Stored AND state in Supabase: {and_instance.name}")
        except Exception as e:
            logger.warning(f"Failed to store AND state in Supabase: {e}")

        return and_data

    async def _allocate_address(
        self,
        and_name: str,
        profile: str,
        ands_spec: Any,
    ) -> str:
        # Find profile definition (profiles is dict[str, ANDProfileDef])
        profile_obj = ands_spec.profiles.get(profile) if ands_spec.profiles else None
        if profile_obj is None:
            raise RuntimeError(f"Profile '{profile}' not found")

        # Sequential /24 allocation within 172.16.0.0/12.
        # Uses ord-sum rather than hash() to avoid collision with >256 ANDs.
        idx = sum(ord(c) for c in and_name) % 4096
        third_octet = idx % 256
        second_extra = idx // 256
        return f"172.{16 + second_extra}.{third_octet}.0/24"

    # ─────────────────────────────────────────────
    # Event-Driven Provisioning
    # ─────────────────────────────────────────────

    async def _consume_org_admission_events(
        self,
        context: PhaseContext,
        docker: DockerHandler,
        gateway: GatewayHandler,
        ands_spec: Any,
    ) -> None:
        """Background consumer for org.admitted events → provision AND."""
        logger = context.logger

        if context.pgmq_client is None:
            logger.info("pgmq_client not available; org admission events disabled")
            return

        logger.info("Starting org admission event consumer")

        while True:
            try:
                msg = await context.pgmq_client.receive(Queue.AND_ADMISSIONS)
                if not msg:
                    await asyncio.sleep(1)
                    continue

                try:
                    envelope = EventEnvelope(**json.loads(msg["message"]))

                    if envelope.event_type != "org.admitted":
                        await context.pgmq_client.delete(Queue.AND_ADMISSIONS, msg["msg_id"])
                        continue

                    payload = envelope.payload
                    org_name = payload.get("org_name")
                    and_profile = payload.get("and_profile", "business")

                    if not org_name:
                        logger.warning("org.admitted event missing org_name")
                        await context.pgmq_client.delete(Queue.AND_ADMISSIONS, msg["msg_id"])
                        continue

                    logger.info(f"Auto-provisioning AND for org: {org_name}")

                    class SyntheticAND:
                        def __init__(self, org: str, profile: str) -> None:
                            self.name = f"{org}-and"
                            self.org = org
                            self.profile = profile
                            self.dns_suffix = f"{org}.internal"

                    and_instance = SyntheticAND(org_name, and_profile)
                    await self._provision_and(context, docker, gateway, and_instance, ands_spec)
                    logger.info(f"Auto-provisioned AND for org {org_name}")
                    await context.pgmq_client.delete(Queue.AND_ADMISSIONS, msg["msg_id"])

                except Exception as e:
                    logger.error(f"Failed to process org admission event: {e}")
                    try:
                        await context.pgmq_client.archive_to_dlq(
                            Queue.AND_ADMISSIONS, msg["msg_id"], str(e)
                        )
                    except Exception as dlq_err:
                        logger.error(f"Failed to archive to DLQ: {dlq_err}")

            except Exception as e:
                logger.error(f"Org admission consumer error: {e}")
                await asyncio.sleep(5)

    # ─────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────

    async def _get_supabase(self):
        from netengine.core.supabase_client import get_db

        return await get_db()

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await emit_event(context, event_type=event_type, emitted_by="ands_handler", payload=payload)
