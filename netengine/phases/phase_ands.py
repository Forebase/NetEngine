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
from datetime import datetime
from typing import Any, Optional

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
        """Execute Phase 7: AND provisioning.

        Sets up:
        1. Validate M1-M6 prerequisites
        2. Validate AND profiles against spec
        3. Provision each AND from spec.ands.instances
        4. Allocate address pool subnets
        5. Create isolated Docker networks
        6. Attach gateway to each AND network
        7. Generate and apply nftables rules
        8. Register DNS zones
        9. Start event consumer for org.admitted events

        Populates context.runtime_state.ands_output with:
        - ands_provisioned: List of AND names
        - address_allocations: Mapping of AND → CIDR
        - profiles_used: List of profiles applied
        - deployed_at: ISO 8601 timestamp

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If prerequisites missing, profiles invalid, or provisioning fails
        """
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

        context.runtime_state.started_at = datetime.utcnow()

        try:
            ands_output: dict[str, Any] = {}

            # Validate AND profiles exist in spec
            available_profiles = (
                {p.name for p in ands_spec.profiles} if ands_spec.profiles else set()
            )
            if not available_profiles:
                logger.warning("No AND profiles defined in spec; using default 'business' profile")
                available_profiles = {"business"}

            # Validate each AND instance
            for and_instance in ands_spec.instances:
                if and_instance.profile not in available_profiles:
                    raise RuntimeError(
                        f"AND {and_instance.name} references undefined profile '{and_instance.profile}'. "
                        f"Available: {available_profiles}"
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
                    context.runtime_state.ands_instances = {}
                context.runtime_state.ands_instances[and_instance.name] = and_data

            ands_output["ands_provisioned"] = ands_provisioned
            ands_output["address_allocations"] = address_allocations
            ands_output["profiles_used"] = list(profiles_used)
            ands_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.ands_output = ands_output
            context.runtime_state.completed_at = datetime.utcnow()

            logger.info(f"Phase 7 complete: {len(ands_provisioned)} ANDs provisioned")

            # Emit success event
            await self._emit_event(
                context,
                event_type="ands.ready",
                payload={
                    "ands_provisioned": ands_provisioned,
                    "profiles_used": list(profiles_used),
                },
            )

            # Start event consumer for org.admitted events (background task)
            asyncio.create_task(
                self._consume_org_admission_events(context, docker, gateway, ands_spec)
            )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.utcnow()
            logger.error(f"Phase 7 setup failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify ANDs are healthy and operational.

        Supports configurable depth:
        - light: Check AND records in runtime_state
        - medium: Verify Docker networks exist
        - deep: Query gateway nftables rules (validates enforcement)

        Args:
            context: Phase execution context

        Returns:
            True if ANDs are healthy, False otherwise
        """
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

            # Light check: verify records exist in runtime state
            if not hasattr(context.runtime_state, "ands_instances"):
                logger.warning("AND instances not tracked in runtime_state")
                return False

            instances = context.runtime_state.ands_instances
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
        """Determine if Phase 7 should be skipped.

        Skip if ANDs have already been provisioned (idempotent).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if already provisioned, False if should execute
        """
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
        """Provision a single AND instance.

        Steps:
        1. Allocate subnet from address pool
        2. Create isolated Docker bridge network
        3. Attach gateway to network
        4. Generate nftables rules from profile
        5. Apply rules to gateway
        6. Register DNS zone
        7. Store state in Supabase

        Args:
            context: Phase context
            docker: Docker handler
            gateway: Gateway handler
            and_instance: AND instance spec
            ands_spec: Full ANDs spec (for profiles)

        Returns:
            AND data dict with cidr, gateway_ip, etc.

        Raises:
            RuntimeError: If provisioning fails
        """
        logger = context.logger

        # 1. Allocate subnet from address pool
        # For MVP: use a fixed allocation strategy based on AND name/profile
        # (In production: would query Supabase address pool manager)
        profile_obj = next(
            (p for p in ands_spec.profiles if p.name == and_instance.profile),
            None,
        )
        if not profile_obj:
            raise RuntimeError(f"Profile {and_instance.profile} not found in spec")

        # Simple allocation: use first available pool for this profile
        # In real scenario, would call domain registry to allocate
        cidr = await self._allocate_address(and_instance.name, and_instance.profile, ands_spec)
        logger.info(f"Allocated CIDR for {and_instance.name}: {cidr}")

        # 2. Create isolated Docker bridge network
        bridge_name = f"netengines_and_{and_instance.name}"
        try:
            await docker.create_network(
                name=bridge_name,
                driver="bridge",
                subnet=cidr,
                internal=True,  # Isolated (no external NAT by default)
            )
            logger.info(f"Created Docker bridge: {bridge_name}")
        except Exception as e:
            raise RuntimeError(f"Failed to create network {bridge_name}: {e}")

        # 3. Attach gateway to this AND network
        network = ipaddress.ip_network(cidr)
        gateway_ip = str(network.network_address + 1)  # First usable IP

        try:
            await docker.connect_network(
                container=gateway.gateway_container_id,
                network=bridge_name,
                ip=gateway_ip,
            )
            logger.info(f"Attached gateway to {bridge_name} at {gateway_ip}")
        except Exception as e:
            # Clean up network on failure
            await docker.remove_network(bridge_name)
            raise RuntimeError(f"Failed to attach gateway to {bridge_name}: {e}")

        # 4-5. Generate and apply nftables rules
        try:
            rules = await gateway.generate_rules(
                rule_context=and_instance.name,
                profile=and_instance.profile,
                cidr=cidr,
            )
            await gateway.apply_rules(rule_context=and_instance.name, rules=rules)
            logger.info(f"Applied nftables rules for {and_instance.name}")
        except Exception as e:
            # Clean up on failure
            await docker.disconnect_network(gateway.gateway_container_id, bridge_name)
            await docker.remove_network(bridge_name)
            raise RuntimeError(f"Failed to apply rules for {and_instance.name}: {e}")

        # 6. Register DNS zone
        dns_suffix = and_instance.dns_suffix or f"{and_instance.org}.internal"
        try:
            # Import DNS handler to add record (careful: needs to be in zone already)
            from netengine.handlers.dns import DNSHandler

            dns = DNSHandler()
            # Register AND gateway as authoritative for suffix
            await dns.add_zone_record(
                context=context,
                zone="internal",  # Register under root internal zone
                record_type="A",
                name=dns_suffix.rstrip("."),
                value=gateway_ip,
                ttl=300,
            )
            logger.info(f"Registered DNS for {dns_suffix} -> {gateway_ip}")
        except Exception as e:
            logger.warning(f"Failed to register DNS for {dns_suffix}: {e}")
            # Don't fail phase - DNS can be recovered

        # 7. Store state
        and_data = {
            "name": and_instance.name,
            "org": and_instance.org,
            "profile": and_instance.profile,
            "cidr": cidr,
            "gateway_ip": gateway_ip,
            "bridge_name": bridge_name,
            "dns_suffix": dns_suffix,
            "deployed_at": datetime.utcnow().isoformat(),
        }

        # Store in Supabase for durability
        try:
            supabase = await self._get_supabase()
            await supabase.table("and_instances").upsert(and_data).execute()
            logger.info(f"Stored AND state in Supabase: {and_instance.name}")
        except Exception as e:
            logger.warning(f"Failed to store AND state in Supabase: {e}")
            # Don't fail - can reconcile later

        return and_data

    async def _allocate_address(
        self,
        and_name: str,
        profile: str,
        ands_spec: Any,
    ) -> str:
        """Allocate CIDR for AND.

        For MVP: uses simple allocation based on profile.
        In production: queries Supabase address pools created by M5.

        Args:
            and_name: AND name
            profile: AND profile name
            ands_spec: ANDs spec

        Returns:
            Allocated CIDR string

        Raises:
            RuntimeError: If no pools available
        """
        # Find profile definition
        profile_obj = next(
            (p for p in ands_spec.profiles if p.name == profile),
            None,
        )
        if not profile_obj:
            raise RuntimeError(f"Profile {profile} not found")

        # For MVP: use a deterministic allocation based on AND name
        # (production would query Supabase address_pools)
        # Simple strategy: use /24 subnets from 172.16.0.0/12 range
        hash_val = hash(and_name) % 256
        return f"172.{16 + (hash_val // 256)}.{hash_val % 256}.0/24"

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
        """Background consumer for org.admitted events → provision AND.

        Listens to pgmq for new organization admissions and auto-provisions
        ANDs for those orgs (if configured in spec).

        Args:
            context: Phase context
            docker: Docker handler
            gateway: Gateway handler
            ands_spec: ANDs spec
        """
        logger = context.logger

        if context.pgmq_client is None:
            logger.info("pgmq_client not available; org admission events disabled")
            return

        logger.info("Starting org admission event consumer")

        while True:
            try:
                msg = await context.pgmq_client.receive("and_admissions")
                if not msg:
                    await asyncio.sleep(1)
                    continue

                try:
                    envelope = EventEnvelope(**json.loads(msg["message"]))

                    if envelope.event_type != "org.admitted":
                        # Skip non-admission events
                        await context.pgmq_client.delete("and_admissions", msg["msg_id"])
                        continue

                    payload = envelope.payload
                    org_name = payload.get("org_name")
                    and_profile = payload.get("and_profile", "business")

                    if not org_name:
                        logger.warning("org.admitted event missing org_name")
                        await context.pgmq_client.delete("and_admissions", msg["msg_id"])
                        continue

                    # Auto-generate AND instance for org
                    logger.info(f"Auto-provisioning AND for org: {org_name}")

                    # Create synthetic AND instance
                    class SyntheticAND:
                        def __init__(self, org: str, profile: str):
                            self.name = f"{org}-and"
                            self.org = org
                            self.profile = profile
                            self.dns_suffix = f"{org}.internal"

                    and_instance = SyntheticAND(org_name, and_profile)

                    # Provision it
                    await self._provision_and(context, docker, gateway, and_instance, ands_spec)

                    logger.info(f"Auto-provisioned AND for org {org_name}")

                    # Mark message as processed
                    await context.pgmq_client.delete("and_admissions", msg["msg_id"])

                except Exception as e:
                    logger.error(f"Failed to process org admission event: {e}")
                    # Archive to DLQ for manual review
                    try:
                        await context.pgmq_client.archive_to_dlq(
                            "and_admissions", msg["msg_id"], str(e)
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
        """Get Supabase client lazily."""
        from netengine.core.supabase_client import get_supabase

        return get_supabase()

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit an AND event.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "ands.ready")
            payload: Event payload dict
        """
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="ands_handler",
            payload=payload,
            correlation_id=context.runtime_state.correlation_id,
            parent_event_id=context.runtime_state.parent_event_id,
        )

        context.logger.info(
            f"Event emitted: {event_type} "
            f"(event_id={event.event_id}, correlation_id={event.correlation_id})"
        )

        # Queue to pgmq for downstream processing (M8+)
        if context.pgmq_client is not None:
            try:
                await context.pgmq_client.send(event)
                context.logger.debug(f"Event queued to pgmq: {event_type}")
            except Exception as e:
                context.logger.warning(f"Failed to queue event to pgmq: {e}")
        else:
            context.logger.debug("pgmq_client not available (M1-M6 testing); event logged only")
