"""Phases 1-2: DNS handler — authoritative DNS root and hierarchy setup.

Responsibilities:
- Deploy DNS root server (Phase 1)
- Configure platform zone (Phase 1)
- Configure TLD servers and zone hierarchies (Phase 2)
- Generate zone files with SOA and NS records
- Write Corefile + zone files to disk
- Deploy CoreDNS container with bind-mounted zone directory
- Verify DNS service is responding
- Emit dns.zones_ready event on success
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from netengine.errors import DNSError
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext

COREDNS_IMAGE = "coredns/coredns:1.11.3"
COREDNS_CONTAINER_NAME = "netengine_coredns"


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
            raise DNSError(
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

            if not context.mock_mode and context.docker_client is not None:
                # Write Corefile + zone files to disk and start CoreDNS container
                zone_dir = await self._write_zone_files_to_disk(
                    context, zone_files, root_zone, platform_zone, tlds_output
                )
                logger.info(f"Zone files written to {zone_dir}")
                container_id = await self._deploy_coredns(context, zone_dir)
                dns_output["coredns_container_id"] = container_id
                # Brief pause for CoreDNS to bind port 53
                await asyncio.sleep(2)

            # Verify DNS service
            dns_healthy = await self._verify_dns_service(context, dns_output)
            dns_output["healthy"] = dns_healthy
            if not dns_healthy:
                raise DNSError("DNS service verification failed")

            dns_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.dns_output = dns_output
            context.runtime_state.phase_completed["1"] = True
            context.runtime_state.phase_completed["2"] = True
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
            context.runtime_state.phase_completed["1"] = True
            context.runtime_state.phase_completed["2"] = True
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
        platform_content = self._generate_platform_zone_file(platform_zone, root_zone, context)
        zone_files["platform.internal"] = platform_content
        logger.debug("Generated platform zone file")

        # TLD zone files
        for tld_name, tld_config in tlds.items():
            tld_content = self._generate_tld_zone_file(tld_name, tld_config, root_zone)
            zone_files[tld_name] = tld_content
            logger.debug(f"Generated {tld_name} zone file")

        return zone_files

    # ─────────────────────────────────────────────
    # Disk + Container Deployment
    # ─────────────────────────────────────────────

    async def _write_zone_files_to_disk(
        self,
        context: PhaseContext,
        zone_files: dict[str, str],
        root_zone: dict[str, Any],
        platform_zone: dict[str, Any],
        tlds: dict[str, Any],
    ) -> Path:
        """Write Corefile and zone files to zone_dir on the host.

        Returns the zone_dir Path used.
        """
        zone_dir = Path(context.zone_dir)
        zones_subdir = zone_dir / "zones"
        zones_subdir.mkdir(parents=True, exist_ok=True)

        # Write individual zone files
        for zone_name, content in zone_files.items():
            zone_path = zones_subdir / zone_name
            await asyncio.to_thread(zone_path.write_text, content)
            context.logger.debug(f"Wrote zone file: {zone_path}")

        # Write Corefile
        corefile_content = self._generate_corefile(zone_files, root_zone, platform_zone, tlds)
        corefile_path = zone_dir / "Corefile"
        await asyncio.to_thread(corefile_path.write_text, corefile_content)
        context.logger.debug(f"Wrote Corefile: {corefile_path}")

        return zone_dir

    def _generate_corefile(
        self,
        zone_files: dict[str, str],
        root_zone: dict[str, Any],
        platform_zone: dict[str, Any],
        tlds: dict[str, Any],
    ) -> str:
        """Generate a CoreDNS Corefile from the zone configuration.

        Each zone gets a `file` plugin stanza pointing at /etc/coredns/zones/<name>.
        A catch-all forward block sends everything else to public resolvers.
        """
        blocks: list[str] = []

        # Upstream forwarder for public DNS (catch-all last)
        blocks.append(
            ". {\n"
            "    forward . 1.1.1.1 8.8.8.8\n"
            "    cache 300\n"
            "    log\n"
            "    errors\n"
            "}"
        )

        # One stanza per zone
        for zone_name in zone_files:
            blocks.append(
                f"{zone_name} {{\n"
                f"    file /etc/coredns/zones/{zone_name}\n"
                f"    reload 10s\n"
                f"    log\n"
                f"    errors\n"
                f"}}"
            )

        return "\n\n".join(blocks) + "\n"

    async def _deploy_coredns(self, context: PhaseContext, zone_dir: Path) -> str:
        """Start the CoreDNS container with the zone directory mounted.

        Returns the container ID. Idempotent: removes existing container first
        if it exists but is stopped.
        """
        import asyncio

        import docker as docker_lib

        client = context.docker_client.client  # type: ignore[union-attr]
        logger = context.logger

        def _sync() -> str:
            # Remove stale stopped container if present
            try:
                existing = client.containers.get(COREDNS_CONTAINER_NAME)
                if existing.status != "running":
                    existing.remove(force=True)
                    logger.debug(f"Removed stale {COREDNS_CONTAINER_NAME} container")
                else:
                    logger.info(f"CoreDNS already running ({existing.id[:12]})")
                    return existing.id
            except docker_lib.errors.NotFound:
                pass

            # Pull image if needed (no-op if present)
            try:
                client.images.get(COREDNS_IMAGE)
            except docker_lib.errors.ImageNotFound:
                logger.info(f"Pulling {COREDNS_IMAGE}...")
                client.images.pull(COREDNS_IMAGE)

            # Listen IP comes from the spec (dns_output not yet set at deploy time)
            root_listen_ip = context.spec.dns.root.listen_ip

            # Create container directly on the core network with the static IP.
            # Docker v1.48+ rejects connecting a container that is already in
            # "none" (private) mode to a second network, so we use the low-level
            # API to attach to core with the desired IP at creation time.
            networking_config = client.api.create_networking_config(
                {"core": client.api.create_endpoint_config(ipv4_address=root_listen_ip)}
            )
            response = client.api.create_container(
                image=COREDNS_IMAGE,
                name=COREDNS_CONTAINER_NAME,
                command=["-conf", "/etc/coredns/Corefile"],
                host_config=client.api.create_host_config(
                    binds={str(zone_dir): {"bind": "/etc/coredns", "mode": "rw"}},
                    restart_policy={"Name": "unless-stopped"},
                ),
                networking_config=networking_config,
            )
            client.api.start(response["Id"])
            return response["Id"]

        container_id: str = await asyncio.to_thread(_sync)
        logger.info(f"CoreDNS container: {container_id[:12]}")
        context.runtime_state.dns_root_container_id = container_id
        return container_id

    def _generate_root_zone_file(
        self,
        root_zone: dict[str, Any],
        platform_zone: dict[str, Any],
        tlds: dict[str, Any],
    ) -> str:
        """Generate root zone file with NS records.

        Delegates to platform zone and TLD servers via NS records.
        Includes stub records for L1 services (auth.internal, ca.internal, etc.)
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
            "; L1 service records (auth.internal, etc. — may be delegated to platform zone)",
            "; These can be updated by M4+ phases",
            f"auth.internal. A {platform_zone['listen_ip']}",
            f"ca.internal. A {platform_zone['listen_ip']}",
            f"registry.internal. A {platform_zone['listen_ip']}",
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
        self,
        platform_zone: dict[str, Any],
        root_zone: dict[str, Any],
        context: "PhaseContext",
    ) -> str:
        """Generate platform zone file with L1 service records."""
        platform_soa = (
            f"{platform_zone['name']}. SOA {root_zone['soa_primary_ns']}. "
            f"root.internal. 1 3600 1800 604800 86400"
        )

        auth_ip = context.spec.identity_platform.listen_ip
        ca_ip = context.spec.pki.acme.listen_ip
        registry_ip = context.spec.world_registry.listen_ip

        lines = [
            f"; Platform zone: {platform_zone['name']}",
            f"; Generated: {datetime.utcnow().isoformat()}",
            platform_soa,
            f"{platform_zone['name']}. NS {platform_zone['ns_server']}.",
            f"{platform_zone['ns_server']}. A {platform_zone['listen_ip']}",
            "",
            "; L1 service records (populated by M4+ handlers)",
            f"auth.{platform_zone['name']}. A {auth_ip}",
            f"ca.{platform_zone['name']}. A {ca_ip}",
            f"registry.{platform_zone['name']}. A {registry_ip}",
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

        In mock mode, only checks that zone data was generated.
        In real mode, issues an actual DNS SOA query against the root zone listen IP.

        Args:
            context: Phase context
            dns_output: DNS output dict to verify

        Returns:
            True if verification passes, False otherwise
        """
        logger = context.logger

        try:
            if "root_zone" not in dns_output or "platform_zone" not in dns_output:
                logger.error("Missing root or platform zone in DNS output")
                return False

            if "zone_files" not in dns_output or not dns_output["zone_files"]:
                logger.error("No zone files were generated")
                return False

            if context.mock_mode or context.docker_client is None:
                logger.info("DNS service verification passed (mock mode)")
                return True

            # Real mode: query the root zone for its SOA record
            root_ip = dns_output["root_zone"].get("listen_ip", "10.0.0.2")
            root_zone_name = dns_output["root_zone"].get("name", "root.internal")
            verified = await self._query_soa(root_ip, root_zone_name, logger)
            if verified:
                logger.info(f"DNS SOA query confirmed at {root_ip}")
            else:
                logger.error(f"DNS SOA query failed for {root_zone_name} at {root_ip}")
            return verified

        except Exception as e:
            logger.error(f"DNS verification failed: {e}")
            return False

    async def _query_soa(self, server_ip: str, zone: str, logger: Any) -> bool:
        """Send a raw DNS SOA query and return True if a valid response arrives."""
        import socket
        import struct

        def _build_query(name: str) -> bytes:
            # Transaction ID, flags (standard query), 1 question, 0 answers
            header = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
            qname = b""
            for label in name.rstrip(".").split("."):
                encoded = label.encode()
                qname += bytes([len(encoded)]) + encoded
            qname += b"\x00"
            # Type SOA (6), Class IN (1)
            question = qname + struct.pack(">HH", 6, 1)
            return header + question

        query = _build_query(zone)
        try:
            loop = asyncio.get_event_loop()

            def _send() -> bytes:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(3)
                    s.sendto(query, (server_ip, 53))
                    data, _ = s.recvfrom(512)
                    return data

            response = await asyncio.wait_for(loop.run_in_executor(None, _send), timeout=5)
            # Response ID should match query ID (0x1234) and QR bit should be set
            resp_id, flags = struct.unpack(">HH", response[:4])
            return resp_id == 0x1234 and bool(flags & 0x8000)
        except Exception as exc:
            logger.warning(f"SOA query to {server_ip} failed: {exc}")
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
            raise DNSError(
                "DNS phase must run before adding records. Call DNS handler execute() first."
            )

        dns_output = context.runtime_state.dns_output
        zone_files = dns_output.get("zone_files", {})

        # Check if zone exists
        if zone not in zone_files:
            raise DNSError(
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

        # Flush to disk so the running CoreDNS container picks up the change
        zone_file_path = Path(context.zone_dir) / "zones" / zone
        if zone_file_path.parent.exists():
            await asyncio.to_thread(zone_file_path.write_text, updated_content)
            logger.debug(f"Zone file flushed to disk: {zone_file_path}")
            # Signal CoreDNS to reload zones (SIGUSR1)
            await self._reload_coredns(context)
        else:
            logger.warning(
                f"Zone directory does not exist, disk flush skipped: {zone_file_path.parent}"
            )

        logger.info(f"Zone record updated: {zone} {record_type} {name} -> {value} (TTL: {ttl})")

    async def _reload_coredns(self, context: "PhaseContext") -> None:
        """Send SIGUSR1 to the CoreDNS container to trigger a zone reload."""
        if context.mock_mode or context.docker_client is None:
            return
        try:
            container = context.docker_client.containers.get(COREDNS_CONTAINER_NAME)
            container.kill(signal="SIGUSR1")
            context.logger.debug("CoreDNS reload signal sent (SIGUSR1)")
        except Exception as e:
            context.logger.warning(f"CoreDNS reload signal failed (non-fatal): {e}")

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
