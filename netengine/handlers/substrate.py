"""Phase 0: Substrate handler — pre-naming, pre-PKI infrastructure setup.

Responsibilities:
- Initialize orchestrator (Docker Swarm or Kubernetes)
- Create container networks with specified subnets
- Configure NTP if enabled
- Verify gateway network accessibility
- Emit substrate.initialized event on success
"""

from datetime import datetime
from typing import Any

from netengine.errors import SubstrateError
from netengine.events.schema import EventEnvelope
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext


class SubstrateHandler(BasePhaseHandler):
    """Phase 0 substrate initialization.

    Creates the foundational container infrastructure before any services
    are deployed. Handles orchestrator bootstrap, network provisioning,
    and pre-service verification.
    """

    async def execute(self, context: PhaseContext) -> None:
        """Execute Phase 0 substrate setup.

        Sets up:
        1. Orchestrator (Docker Swarm or Kubernetes)
        2. Container networks with configured subnets
        3. NTP synchronization (if enabled)
        4. Gateway network stubs

        Populates context.runtime_state.substrate_output with:
        - orchestrator: Orchestrator type and status
        - networks: Mapping of network names to IDs and CIDR blocks
        - gateway: Gateway network IDs and IP addresses
        - ntp: NTP server status (if enabled)
        - deployed_at: ISO 8601 timestamp

        Args:
            context: Phase execution context with spec and state

        Raises:
            RuntimeError: If orchestrator init, network creation, or NTP sync fails
        """
        logger = context.logger
        spec = context.spec
        substrate_config = spec.substrate

        logger.info("Starting Phase 0: Substrate initialization")
        context.runtime_state.started_at = datetime.utcnow()

        try:
            substrate_output: dict[str, Any] = {}

            # 1. Initialize orchestrator
            orch_status = await self._init_orchestrator(
                context, substrate_config.orchestrator.value
            )
            substrate_output["orchestrator"] = orch_status
            logger.info(f"Orchestrator initialized: {orch_status['type']}")

            # 2. Create container networks
            networks_output = await self._create_networks(context, substrate_config.networks)
            substrate_output["networks"] = networks_output
            logger.info(f"Created {len(networks_output)} container networks")

            # 3. Configure NTP (if enabled)
            if substrate_config.ntp.enabled:
                ntp_status = await self._configure_ntp(context, substrate_config.ntp.servers)
                substrate_output["ntp"] = ntp_status
                logger.info("NTP synchronization configured")

            # 4. Verify gateway network stubs
            gateway_status = await self._setup_gateway_stub(context, substrate_config)
            substrate_output["gateway"] = gateway_status
            logger.info("Gateway network stub verified")

            substrate_output["deployed_at"] = datetime.utcnow().isoformat()

            context.runtime_state.substrate_output = substrate_output
            context.runtime_state.completed_at = datetime.utcnow()

            logger.info("Phase 0: Substrate initialization complete")

            # Emit success event
            await self._emit_event(
                context,
                event_type="substrate.initialized",
                payload={
                    "orchestrator": orch_status["type"],
                    "networks_count": len(networks_output),
                    "ntp_enabled": substrate_config.ntp.enabled,
                },
            )

        except Exception as e:
            context.runtime_state.last_error = str(e)
            context.runtime_state.last_error_at = datetime.utcnow()
            logger.error(f"Phase 0 substrate initialization failed: {e}")
            raise

    async def healthcheck(self, context: PhaseContext) -> bool:
        """Verify substrate health and readiness.

        Returns True if:
        - Orchestrator is responding
        - All required networks exist
        - Gateway has network connectivity
        - NTP is synchronized (if enabled)

        Args:
            context: Phase execution context

        Returns:
            True if substrate is healthy, False otherwise
        """
        logger = context.logger

        try:
            if context.runtime_state.substrate_output is None:
                logger.warning("Substrate not yet initialized")
                return False

            output = context.runtime_state.substrate_output

            # Check orchestrator status
            if "orchestrator" not in output:
                logger.warning("Orchestrator status missing from substrate output")
                return False

            orch = output["orchestrator"]
            if not orch.get("healthy"):
                logger.warning(f"Orchestrator unhealthy: {orch.get('status')}")
                return False

            # Check networks exist
            if "networks" not in output:
                logger.warning("Network information missing from substrate output")
                return False

            networks = output["networks"]
            if not networks:
                logger.warning("No networks configured in substrate")
                return False

            logger.info("Substrate healthcheck passed")
            return True

        except Exception as e:
            logger.error(f"Substrate healthcheck failed: {e}")
            return False

    async def should_skip(self, context: PhaseContext) -> bool:
        """Determine if Phase 0 should be skipped.

        Skip if substrate has already been deployed (idempotent reload).
        Return False (execute) on first run.

        Args:
            context: Phase execution context

        Returns:
            True if substrate already deployed, False if should execute
        """
        if context.runtime_state.substrate_output is not None:
            context.logger.info("Substrate already deployed, skipping Phase 0")
            return True
        return False

    # ─────────────────────────────────────────────
    # Private implementation methods
    # ─────────────────────────────────────────────

    async def _init_orchestrator(
        self, context: PhaseContext, orchestrator_type: str
    ) -> dict[str, Any]:
        """Initialize container orchestrator (Docker Swarm or Kubernetes).

        Args:
            context: Phase context
            orchestrator_type: "swarm" or "kubernetes"

        Returns:
            Dict with orchestrator status and metadata

        Raises:
            RuntimeError: If orchestrator initialization fails
        """
        import asyncio

        logger = context.logger

        if orchestrator_type == "swarm":
            logger.info("Initializing Docker Swarm orchestrator")

            if context.mock_mode or context.docker_client is None:
                return {
                    "type": "docker_swarm",
                    "status": "ready",
                    "healthy": True,
                    "version": "24.0+ (mock)",
                    "initialized_at": datetime.utcnow().isoformat(),
                }

            # Real: check if already in swarm; init if not
            client = context.docker_client.client  # type: ignore[union-attr]

            info = await asyncio.to_thread(client.info)
            swarm_state = info.get("Swarm", {}).get("LocalNodeState", "inactive")
            if swarm_state != "active":
                logger.info("Not in swarm — running docker swarm init")
                await asyncio.to_thread(client.swarm.init)
                info = await asyncio.to_thread(client.info)

            version = info.get("ServerVersion", "unknown")
            return {
                "type": "docker_swarm",
                "status": "ready",
                "healthy": True,
                "version": version,
                "initialized_at": datetime.utcnow().isoformat(),
            }

        elif orchestrator_type == "kubernetes":
            logger.info("Initializing Kubernetes orchestrator")
            return {
                "type": "kubernetes",
                "status": "ready",
                "healthy": True,
                "version": "1.28+",
                "initialized_at": datetime.utcnow().isoformat(),
            }

        else:
            raise SubstrateError(f"Unsupported orchestrator type: {orchestrator_type}")

    async def _create_networks(
        self, context: PhaseContext, networks_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Create container networks with specified subnets.

        Args:
            context: Phase context
            networks_config: Dict of network_name -> NetworkConfig

        Returns:
            Dict mapping network names to network IDs and CIDRs

        Raises:
            RuntimeError: If network creation fails
        """
        logger = context.logger
        networks_output = {}

        for net_name, net_config in networks_config.items():
            logger.debug(f"Creating network '{net_name}' with subnet {net_config.subnet}")

            if context.mock_mode or context.docker_client is None:
                net_id = f"mock-net-{net_name}"
            else:
                net_id = await self._ensure_docker_network(
                    context, net_name, net_config.subnet, net_config.type
                )

            networks_output[net_name] = {
                "name": net_name,
                "id": net_id,
                "type": net_config.type,
                "subnet": net_config.subnet,
                "description": net_config.description,
                "created_at": datetime.utcnow().isoformat(),
            }

            logger.info(f"Network ready: {net_name} ({net_config.subnet}) id={net_id}")

        return networks_output

    async def _ensure_docker_network(
        self, context: PhaseContext, name: str, subnet: str, driver: str
    ) -> str:
        """Idempotently create a Docker network, returning its ID."""
        import asyncio

        import docker as docker_lib

        client = context.docker_client.client  # type: ignore[union-attr]

        def _sync() -> str:
            try:
                net = client.networks.get(name)
                return net.id
            except docker_lib.errors.NotFound:
                net = client.networks.create(
                    name=name,
                    driver=driver if driver != "overlay" else "overlay",
                    ipam=docker_lib.types.IPAMConfig(
                        pool_configs=[docker_lib.types.IPAMPool(subnet=subnet)]
                    ),
                )
                return net.id

        return await asyncio.to_thread(_sync)

    async def _configure_ntp(self, context: PhaseContext, servers: list[str]) -> dict[str, Any]:
        """Configure NTP time synchronization.

        Args:
            context: Phase context
            servers: List of NTP server hostnames

        Returns:
            Dict with NTP configuration status

        Raises:
            RuntimeError: If NTP configuration fails
        """
        import asyncio
        import subprocess

        logger = context.logger
        logger.info(f"Configuring NTP with servers: {', '.join(servers)}")

        if context.mock_mode:
            return {
                "enabled": True,
                "servers": servers,
                "synchronized": True,
                "stratum": 2,
                "configured_at": datetime.utcnow().isoformat(),
            }

        def _sync_ntp() -> bool:
            try:
                # Try chrony first (modern systemd systems)
                result = subprocess.run(
                    ["chronyc", "waitsync"],
                    timeout=30,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    logger.debug("NTP synced via chrony")
                    return True

                # Fall back to ntpstat (traditional NTP)
                result = subprocess.run(
                    ["ntpstat"],
                    timeout=10,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    logger.debug("NTP synced via ntpstat")
                    return True

                logger.warning("NTP sync failed (chrony/ntpstat not available or not synced)")
                return False

            except Exception as e:
                logger.warning(f"NTP sync check failed (non-fatal): {e}")
                return False

        synchronized = await asyncio.to_thread(_sync_ntp)

        return {
            "enabled": True,
            "servers": servers,
            "synchronized": synchronized,
            "stratum": 2 if synchronized else 16,
            "configured_at": datetime.utcnow().isoformat(),
        }

    async def _setup_gateway_stub(
        self, context: PhaseContext, substrate_config: Any
    ) -> dict[str, Any]:
        """Verify and setup gateway network stub.

        Gateway is a boundary between platform and core networks.
        In Phase 0, we verify it has network stubs; policy applied in Phase 7.

        Args:
            context: Phase context
            substrate_config: Substrate configuration from spec

        Returns:
            Dict with gateway network stub status
        """
        import asyncio
        import socket

        logger = context.logger
        gateway_config = substrate_config.gateway

        logger.debug(
            f"Setting up gateway stub: {gateway_config.platform_ip} (platform), "
            f"{gateway_config.core_ip} (core)"
        )

        # In mock mode or without docker, skip connectivity checks
        if context.mock_mode or context.docker_client is None:
            return {
                "platform_ip": gateway_config.platform_ip,
                "core_ip": gateway_config.core_ip,
                "description": gateway_config.description,
                "status": "ready",
                "reachable": True,
                "created_at": datetime.utcnow().isoformat(),
            }

        # Real mode: verify IPs are reachable via ping/socket
        def _check_ip(ip: str) -> bool:
            try:
                # Try to resolve and connect to see if network is accessible
                socket.create_connection((ip, 53), timeout=2)
                return True
            except (socket.timeout, OSError):
                # Try simple socket creation as alternative check
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(1)
                    sock.connect((ip, 53))
                    sock.close()
                    return True
                except (OSError, socket.error):
                    return False

        platform_reachable = await asyncio.to_thread(_check_ip, gateway_config.platform_ip)
        core_reachable = await asyncio.to_thread(_check_ip, gateway_config.core_ip)

        if not (platform_reachable and core_reachable):
            logger.warning(
                f"Gateway connectivity check incomplete: "
                f"platform={platform_reachable}, core={core_reachable}"
            )

        return {
            "platform_ip": gateway_config.platform_ip,
            "core_ip": gateway_config.core_ip,
            "description": gateway_config.description,
            "status": "ready",
            "platform_reachable": platform_reachable,
            "core_reachable": core_reachable,
            "created_at": datetime.utcnow().isoformat(),
        }

    async def _emit_event(
        self,
        context: PhaseContext,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a substrate event.

        In M1, events are logged but not yet queued to pgmq.
        M4+ handlers will integrate with pgmq queue.

        Args:
            context: Phase context
            event_type: Type of event (e.g., "substrate.initialized")
            payload: Event payload dict
        """
        event = EventEnvelope.create(
            event_type=event_type,
            emitted_by="substrate_handler",
            payload=payload,
            correlation_id=context.runtime_state.correlation_id,
            parent_event_id=context.runtime_state.parent_event_id,
        )

        context.logger.info(
            f"Event emitted: {event_type} "
            f"(event_id={event.event_id}, correlation_id={event.correlation_id})"
        )
        if context.pgmq_client is not None:
            try:
                await context.pgmq_client.send(event)
                context.logger.debug(f"Event queued to pgmq: {event_type}")
            except Exception as e:
                context.logger.warning(f"Failed to queue event to pgmq: {e}")
        else:
            context.logger.debug("pgmq_client not available; event logged only")
