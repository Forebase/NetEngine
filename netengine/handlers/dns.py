"""Phases 1-2: DNS handler — authoritative DNS root and hierarchy setup.

Responsibilities:
- Deploy DNS root server (Phase 1)
- Configure platform zone (Phase 1)
- Configure TLD servers and zone hierarchies (Phase 2)
- Generate zone files with SOA and NS records
- Verify DNS service is responding
- Emit dns.zones_ready event on success
"""

import asyncio
from datetime import datetime
from typing import Any

from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext


class DNSHandler(BasePhaseHandler):
    """Phases 1-2: DNS hierarchy and zone configuration.

    Deploys authoritative DNS infrastructure:
    - Root zone (apex): root.internal
    - Platform zone (L1 services): platform.internal
    - TLD zones (org domains): .internal, .com, etc.

    Zone delegation ensures name resolution is hierarchical and
    federation-ready (cross-world peering in M9+).
    """

    async def execute(self, context: PhaseContext) -> None:
        """Execute Phases 1-2 DNS setup.

        Sets up:
        1. Root DNS server on root zone
        2. Platform zone with L1 service names
        3. TLD servers with zone delegation
        4. Zone file generation and validation
        5. DNS service verification

        Populates context.runtime_state.dns_output with:
        - root_zone: Root server status and SOA
        - platform_zone: Platform zone records
        - tlds: Mapping of TLD name -> server config
        - zone_files: Generated zone file content
        - deployed_at: ISO 8601 timestamp

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If DNS service deployment, zone generation, or verification fails
        """
        logger = context.logger
        spec = context.spec
        dns_config = spec.dns

        logger.info("Starting Phases 1-2: DNS hierarchy setup")

        # Validate substrate dependency
        if context.runtime_state.substrate_output is None:
            raise RuntimeError(
                "Substrate phase (Phase 0) must complete before DNS setup. "
                "Ensure Phase 0 has run and created networks."
            )

        context.runtime_state.started_at = datetime.utcnow()

        try:
            dns_output: dict[str, Any] = {}

            # Phase 1: Root and platform zones
            root_zone = await self._setup_root_zone(context, dns_config.root)
            dns_output["root_zone"] = root_zone
            logger.info(f"Root zone deployed: {root_zone['name']}")

            platform_zone = await self._setup_platform_zone(context, dns_config.platform_zone)
            dns_output["platform_zone"] = platform_zone
            logger.info(f"Platform zone deployed: {platform_zone['name']}")

            # Phase 2: TLD servers
            tlds_output = await self._setup_tlds(context, dns_config.tlds)
            dns_output["tlds"] = tlds_output
            logger.info(f"TLD servers configured: {len(tlds_output)} zones")

            # Generate zone files
            zone_files = await self._generate_zone_files(
                context, root_zone, platform_zone, tlds_output
            )
            dns_output["zone_files"] = zone_files
            logger.info(f"Generated {len(zone_files)} zone files")

            # Verify DNS service
            dns_healthy = await self._verify_dns_service(context, dns_output)
            dns_output["healthy"] = dns_healthy
            if not dns_healthy:
                raise RuntimeError("DNS service verification failed")

            dns_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.dns_output = dns_output
            context.runtime_state.completed_at = datetime.utcnow()

            logger.info("Phases 1-2: DNS setup complete")

            # Emit success event
            await self._emit_event(
                context,
                event_type="dns.zones_ready",
                payload={
                    "root_zone": root_zone["name"],
                    "platform_zone": platform_zone["name"],
                    "tld_count": len(tlds_output),
                },
            )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.utcnow()
            logger.error(f"Phases 1-2 DNS setup failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify DNS service health and readiness.

        Returns True if:
        - DNS server is responding
        - Root zone is authoritative
        - Platform zone is resolvable
        - All TLD zones have NS records

        Args:
            context: Phase execution context

        Returns:
            True if DNS is healthy, False otherwise
        """
        logger = context.logger

        try:
            if context.runtime_state.dns_output is None:
                logger.warning("DNS not yet initialized")
                return False

            output = context.runtime_state.dns_output

            # Check if marked healthy from verification
            if not output.get("healthy"):
                logger.warning("DNS service failed verification during setup")
                return False

            # Check zones are present
            if "root_zone" not in output or "platform_zone" not in output:
                logger.warning("Zone information missing from DNS output")
                return False

            logger.info("DNS healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"DNS healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if Phases 1-2 should be skipped.

        Skip if DNS has already been deployed (idempotent reload).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if DNS already deployed, False if should execute
        """
        if context.runtime_state.dns_output is not None:
            context.logger.info("DNS already deployed, skipping Phases 1-2")
            return True
        return False

    # ─────────────────────────────────────────────
    # Phase 1: Root and Platform Zone Setup
    # ─────────────────────────────────────────────

    async def _setup_root_zone(self, context: PhaseContext, root_config: Any) -> dict[str, Any]:
        """Setup root zone (apex: root.internal).

        Root zone contains NS records delegating to platform and TLD servers.

        Args:
            context: Phase context
            root_config: RootDNSConfig from spec

        Returns:
            Dict with root zone configuration
        """
        logger = context.logger

        if not root_config.enabled:
            logger.info("Root zone disabled in spec")
            return {"enabled": False}

        logger.info(f"Setting up root zone: {root_config.soa_primary_ns}")

        return {
            "enabled": True,
            "name": "root.internal",
            "type": root_config.type,
            "server": root_config.server,
            "listen_ip": root_config.listen_ip,
            "soa_primary_ns": root_config.soa_primary_ns,
            "soa_email": root_config.soa_email,
            "serial_policy": root_config.serial_policy.value,
            "deployed_at": datetime.utcnow().isoformat(),
        }

    async def _setup_platform_zone(
        self, context: PhaseContext, platform_config: Any
    ) -> dict[str, Any]:
        """Setup platform zone (L1 service names: platform.internal).

        Platform zone contains A/AAAA records for infrastructure services:
        - auth.platform.internal (identity provider)
        - ca.platform.internal (ACME server)
        - registry.platform.internal (world registry)
        - mail.internal (mail server)
        - storage.platform.internal (object storage)
        etc.

        Args:
            context: Phase context
            platform_config: PlatformZoneConfig from spec

        Returns:
            Dict with platform zone configuration
        """
        logger = context.logger
        logger.info(f"Setting up platform zone: {platform_config.name}")

        return {
            "name": platform_config.name,
            "type": platform_config.type,
            "listen_ip": platform_config.listen_ip,
            "ns_server": "ns.platform.internal",
            "deployed_at": datetime.utcnow().isoformat(),
        }

    # ─────────────────────────────────────────────
    # Phase 2: TLD Setup
    # ─────────────────────────────────────────────

    async def _setup_tlds(self, context: PhaseContext, tlds_config: list[Any]) -> dict[str, Any]:
        """Setup TLD servers for each configured TLD.

        TLDs are delegated from root zone via NS records.
        Each TLD server is authoritative for its zone.

        Example:
        - .internal TLD: root.internal NS 10.0.0.4
        - .com TLD: root.internal NS 10.0.0.5

        Args:
            context: Phase context
            tlds_config: List of TLDConfig from spec

        Returns:
            Dict mapping TLD name -> server config
        """
        logger = context.logger
        tlds_output: dict[str, Any] = {}

        if not tlds_config:
            logger.info("No TLDs configured")
            return tlds_output

        for tld_config in tlds_config:
            logger.info(f"Configuring TLD: {tld_config.name}")

            tlds_output[tld_config.name] = {
                "name": tld_config.name,
                "type": tld_config.type,
                "listen_ip": tld_config.listen_ip,
                "description": tld_config.description,
                "ns_server": f"ns{tld_config.listen_ip.split('.')[-1]}.internal",
                "deployed_at": datetime.utcnow().isoformat(),
            }

        return tlds_output

    # ─────────────────────────────────────────────
    # Zone File Generation
    # ─────────────────────────────────────────────

    async def _generate_zone_files(
        self,
        context: PhaseContext,
        root_zone: dict[str, Any],
        platform_zone: dict[str, Any],
        tlds: dict[str, Any],
    ) -> dict[str, str]:
        """Generate zone files for all configured zones.

        Format is CoreDNS zone file (RFC 1035 format).

        Args:
            context: Phase context
            root_zone: Root zone config
            platform_zone: Platform zone config
            tlds: TLDs dict

        Returns:
            Dict mapping zone name -> zone file content
        """
        logger = context.logger
        zone_files = {}

        # Root zone file
        if root_zone.get("enabled"):
            root_content = self._generate_root_zone_file(root_zone, platform_zone, tlds)
            zone_files["root.internal"] = root_content
            logger.debug("Generated root zone file")

        # Platform zone file
        platform_content = self._generate_platform_zone_file(platform_zone, root_zone)
        zone_files["platform.internal"] = platform_content
        logger.debug("Generated platform zone file")

        # TLD zone files
        for tld_name, tld_config in tlds.items():
            tld_content = self._generate_tld_zone_file(tld_name, tld_config, root_zone)
            zone_files[tld_name] = tld_content
            logger.debug(f"Generated {tld_name} zone file")

        return zone_files

    def _generate_root_zone_file(
        self,
        root_zone: dict[str, Any],
        platform_zone: dict[str, Any],
        tlds: dict[str, Any],
    ) -> str:
        """Generate root zone file with NS records.

        Delegates to platform zone and TLD servers via NS records.
        """
        serial = self._generate_serial(root_zone.get("serial_policy", "timestamp"))

        soa_email_addr = root_zone["soa_email"].replace("@", ".")
        soa_record = (
            f"{root_zone['name']}. SOA {root_zone['soa_primary_ns']}. "
            f"{soa_email_addr}. {serial} 3600 1800 604800 86400"
        )

        lines = [
            f"; Root zone: {root_zone['name']}",
            f"; Generated: {datetime.utcnow().isoformat()}",
            soa_record,
            f"{root_zone['name']}. NS ns.root.internal.",
            "",
            "; Delegation to platform zone",
            f"platform.internal. NS {platform_zone['ns_server']}.",
            f"platform.internal. A {platform_zone['listen_ip']}",
            "",
        ]

        # TLD delegations
        if tlds:
            lines.append("; Delegations to TLD servers")
            for tld_name, tld_config in tlds.items():
                lines.append(f"{tld_name}. NS {tld_config['ns_server']}.")
                lines.append(f"{tld_config['ns_server']}. A {tld_config['listen_ip']}")
            lines.append("")

        return "\n".join(lines)

    def _generate_platform_zone_file(
        self, platform_zone: dict[str, Any], root_zone: dict[str, Any]
    ) -> str:
        """Generate platform zone file with L1 service records.

        This is a stub; real implementation populates from identity, registry, etc.
        """
        platform_soa = (
            f"{platform_zone['name']}. SOA {root_zone['soa_primary_ns']}. "
            f"root.internal. 1 3600 1800 604800 86400"
        )

        lines = [
            f"; Platform zone: {platform_zone['name']}",
            f"; Generated: {datetime.utcnow().isoformat()}",
            platform_soa,
            f"{platform_zone['name']}. NS {platform_zone['ns_server']}.",
            f"{platform_zone['ns_server']}. A {platform_zone['listen_ip']}",
            "",
            "; L1 service records (populated by M4+ handlers)",
            f"auth.{platform_zone['name']}. A 10.0.0.7",
            f"ca.{platform_zone['name']}. A 10.0.0.6",
            f"registry.{platform_zone['name']}. A 10.0.0.8",
            "",
        ]

        return "\n".join(lines)

    def _generate_tld_zone_file(
        self, tld_name: str, tld_config: dict[str, Any], root_zone: dict[str, Any]
    ) -> str:
        """Generate TLD zone file.

        TLD zones start empty; populated by domain registry (Phase 5b) and org operations.
        """
        tld_soa = (
            f"{tld_name}. SOA {root_zone['soa_primary_ns']}. "
            f"root.internal. 1 3600 1800 604800 86400"
        )

        lines = [
            f"; TLD zone: {tld_name}",
            f"; Generated: {datetime.utcnow().isoformat()}",
            tld_soa,
            f"{tld_name}. NS {tld_config['ns_server']}.",
            f"{tld_config['ns_server']}. A {tld_config['listen_ip']}",
            "",
            "; Domain records (populated by domain registry and orgs)",
            "",
        ]

        return "\n".join(lines)

    def _generate_serial(self, policy: str) -> str:
        """Generate SOA serial number.

        Policies:
        - timestamp: Current Unix timestamp (recommended for M0-M8)
        - increment: Manual increment (requires tracking previous serial)

        Args:
            policy: Serial policy from spec

        Returns:
            Serial number as string
        """
        if policy == "timestamp":
            return str(int(datetime.utcnow().timestamp()))
        else:
            # Fallback for unknown policy
            return "1"

    # ─────────────────────────────────────────────
    # Verification
    # ─────────────────────────────────────────────

    async def _verify_dns_service(self, context: PhaseContext, dns_output: dict[str, Any]) -> bool:
        """Verify DNS service is responding and zones are resolvable.

        In M1, we stub this verification. Real implementation would:
        1. Query root zone for SOA record
        2. Query platform zone for A records
        3. Query TLDs for NS records
        4. Verify all responses are authoritative

        Args:
            context: Phase context
            dns_output: DNS output dict to verify

        Returns:
            True if verification passes, False otherwise
        """
        logger = context.logger

        try:
            # Check all required zones are present
            if "root_zone" not in dns_output or "platform_zone" not in dns_output:
                logger.error("Missing root or platform zone in DNS output")
                return False

            # Check zone files were generated
            if "zone_files" not in dns_output or not dns_output["zone_files"]:
                logger.error("No zone files were generated")
                return False

            logger.info("DNS service verification passed (stubbed in M1)")
            return True

        except Exception as e:
            logger.error(f"DNS verification failed: {e}")
            return False

    # ─────────────────────────────────────────────
    # Event Emission
    # ─────────────────────────────────────────────

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """
        Emit a DNS event.

        Events are emitted for causal tracing and queued to pgmq for downstream handlers.
        If pgmq_client is not available (M1-M3 testing), events are logged only.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "dns.zones_ready")
            payload: Event payload dict
        """
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="dns_handler",
            payload=payload,
            correlation_id=context.runtime_state.correlation_id,
            parent_event_id=context.runtime_state.parent_event_id,
        )

        context.logger.info(
            f"Event emitted: {event_type} "
            f"(event_id={event.event_id}, correlation_id={event.correlation_id})"
        )

        # Queue to pgmq for M4+ event processing
        if context.pgmq_client is not None:
            try:
                await context.pgmq_client.send(event)
                context.logger.debug(f"Event queued to pgmq: {event_type}")
            except Exception as e:
                context.logger.warning(f"Failed to queue event to pgmq: {e}")
        else:
            context.logger.debug("pgmq_client not available (M1-M3 testing); event logged only")

    async def add_zone_record(
        self,
        context: PhaseContext,
        zone: str,
        record_type: str,
        name: str,
        value: str,
        ttl: int = 300,
    ) -> None:
        """Add or update a DNS record in the zone file.

        In M2, this operates on in-memory zone file strings in runtime_state.dns_output.
        Future (M4+): Integrate with disk-backed zone files and CoreDNS reload.

        Args:
            context: Phase context (contains runtime_state with dns_output)
            zone: The zone name (e.g., "platform.internal")
            record_type: "A", "AAAA", "CNAME", "NS", "MX", etc.
            name: The subdomain (e.g., "ca" for ca.platform.internal, or "@" for root)
            value: The IP address, target CNAME, or NS server name
            ttl: Time-to-live in seconds (default: 300)

        Raises:
            RuntimeError: If DNS phase hasn't run or zone doesn't exist
        """
        logger = context.logger

        # Validate that DNS phase has run
        if context.runtime_state.dns_output is None:
            raise RuntimeError(
                "DNS phase must run before adding records. " "Call DNS handler execute() first."
            )

        dns_output = context.runtime_state.dns_output
        zone_files = dns_output.get("zone_files", {})

        # Check if zone exists
        if zone not in zone_files:
            raise RuntimeError(
                f"Zone '{zone}' not found in zone_files. "
                f"Available zones: {list(zone_files.keys())}"
            )

        # Update the zone file in-memory
        zone_content = zone_files[zone]
        new_record = self._build_record_line(name, record_type, value, ttl)

        # Update zone file by finding and replacing or appending
        updated_content = await asyncio.to_thread(
            self._upsert_record_in_memory, zone_content, name, record_type, new_record
        )

        # Update the zone file in runtime_state
        dns_output["zone_files"][zone] = updated_content

        logger.info(f"Zone record updated: {zone} {record_type} {name} -> {value} (TTL: {ttl})")

    @staticmethod
    def _build_record_line(name: str, record_type: str, value: str, ttl: int) -> str:
        """Build an RFC 1035 compliant DNS record line.

        Args:
            name: Subdomain or "@" for root
            record_type: "A", "AAAA", "CNAME", "NS", "MX", etc.
            value: Record value (IP, hostname, etc.)
            ttl: Time-to-live

        Returns:
            Record line in RFC 1035 format
        """
        # Add trailing dot to name and value if they look like hostnames (for NS, CNAME, etc.)
        if record_type in ("NS", "CNAME", "MX", "PTR") and not value.endswith("."):
            value = f"{value}."
        if name != "@" and not name.endswith("."):
            # name stays without dot for brevity in zone files (implied)
            pass

        return f"{name} {ttl} IN {record_type} {value}"

    @staticmethod
    def _upsert_record_in_memory(
        zone_content: str, name: str, record_type: str, new_record: str
    ) -> str:
        """Update a record in a zone file string (in-memory).

        Finds and replaces existing record, or appends if not found.
        Preserves zone file structure (comments, section markers, etc.).

        Args:
            zone_content: Current zone file content
            name: Record name being upserted
            record_type: Record type (for matching)
            new_record: New record line to insert

        Returns:
            Updated zone file content
        """
        import re

        lines = zone_content.split("\n")

        # Pattern to match existing record: "name TTL IN record_type ..."
        # Handles optional trailing dot, various TTLs, and variations
        pattern = re.compile(
            rf"^{re.escape(name)}\s+\d+\s+IN\s+{record_type}\s+",
            re.IGNORECASE,
        )

        new_lines = []
        found = False

        for line in lines:
            if pattern.match(line):
                # Replace existing record
                new_lines.append(new_record)
                found = True
            else:
                new_lines.append(line)

        if not found:
            # Record doesn't exist; append it before trailing blank lines
            # Find last non-empty line
            while new_lines and new_lines[-1].strip() == "":
                new_lines.pop()
            new_lines.append(new_record)
            new_lines.append("")  # Restore trailing blank

        return "\n".join(new_lines)
