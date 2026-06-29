# netengines/handlers/docker_handler.py
import asyncio
import os
from typing import Any

import docker


class DockerHandler:
    def __init__(self) -> None:
        self.client = docker.from_env()

    @property
    def containers(self) -> Any:
        """Expose Docker SDK containers for legacy handlers during adapter migration."""
        return self.client.containers

    async def ensure_volume(self, name: str) -> None:
        """Create a named volume if it doesn't exist."""
        await asyncio.to_thread(self._ensure_volume_sync, name)

    def _ensure_volume_sync(self, name: str) -> None:
        try:
            self.client.volumes.get(name)
        except docker.errors.NotFound:
            self.client.volumes.create(name)

    # In DockerHandler
    async def run_container_one_off(
        self,
        image: str,
        command: list[str] | str,
        volumes: dict[str, Any],
        environment: dict[str, str] | None,
        working_dir: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._run_container_one_off_sync,
            image,
            command,
            volumes,
            environment,
            working_dir,
            **kwargs,
        )

    def _run_container_one_off_sync(
        self,
        image: str,
        command: list[str] | str,
        volumes: dict[str, Any],
        environment: dict[str, str] | None,
        working_dir: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        container = self.client.containers.run(
            image=image,
            command=command,
            volumes=volumes,
            environment=environment,
            remove=False,
            detach=True,
            working_dir=working_dir,
            **kwargs,
        )
        result = container.wait()
        logs = container.logs().decode()
        container.remove()
        return {"exit_code": result["StatusCode"], "logs": logs}

    async def start_container(
        self,
        name: str,
        image: str,
        command: list[str] | str | None,
        volumes: dict[str, Any],
        network: str | None,
        ip: str | None,
        environment: dict[str, str] | None,
        **kwargs: Any,
    ) -> str:
        """Start a long‑running container attached to a network with a fixed IP."""
        return await asyncio.to_thread(
            self._start_container_sync,
            name,
            image,
            command,
            volumes,
            network,
            ip,
            environment,
            **kwargs,
        )

    def _start_container_sync(
        self,
        name: str,
        image: str,
        command: list[str] | str | None,
        volumes: dict[str, Any],
        network: str | None,
        ip: str | None,
        environment: dict[str, str] | None,
        **kwargs: Any,
    ) -> str:
        # Ensure network exists (we assume it was created in Phase 0)
        net = self.client.networks.get(network) if network is not None else None
        container = self.client.containers.run(
            image=image,
            command=command,
            name=name,
            volumes=volumes,
            environment=environment,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            **kwargs,
        )
        # Attach to network with specific IP
        if net is not None:
            net.connect(container, ipv4_address=ip)
        return str(container.id)

    async def exec_command(self, container_id: str, cmd: list[str]) -> tuple[int, str]:
        """Execute a command inside a running container."""
        return await asyncio.to_thread(self._exec_command_sync, container_id, cmd)

    def _exec_command_sync(self, container_id: str, cmd: list[str]) -> tuple[int, str]:
        container = self.client.containers.get(container_id)
        exec_result = container.exec_run(cmd, demux=False)
        output = exec_result.output or b""
        return exec_result.exit_code, output.decode("utf-8", errors="replace")

    async def stop_container(self, container_id: str) -> None:
        await asyncio.to_thread(self._stop_container_sync, container_id)

    def _stop_container_sync(self, container_id: str) -> None:
        container = self.client.containers.get(container_id)
        container.stop(timeout=10)
        container.remove()

    async def create_network(
        self, name: str, driver: str = "bridge", subnet: str | None = None, internal: bool = False
    ) -> None:
        """Create a Docker network."""
        await asyncio.to_thread(self._create_network_sync, name, driver, subnet, internal)

    def _create_network_sync(
        self, name: str, driver: str, subnet: str | None, internal: bool
    ) -> None:
        ipam_pool = None
        if subnet:
            ipam_pool = docker.types.IPAMPool(subnet=subnet)
            ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])
        else:
            ipam_config = None
        self.client.networks.create(name=name, driver=driver, internal=internal, ipam=ipam_config)

    async def connect_network(self, container: str, network: str, ip: str | None) -> None:
        await asyncio.to_thread(self._connect_network_sync, container, network, ip)

    def _connect_network_sync(self, container: str, network: str, ip: str | None) -> None:
        net = self.client.networks.get(network) if network is not None else None
        if net is not None:
            net.connect(container, ipv4_address=ip)

    async def disconnect_network(self, container: str, network: str) -> None:
        await asyncio.to_thread(self._disconnect_network_sync, container, network)

    def _disconnect_network_sync(self, container: str, network: str) -> None:
        net = self.client.networks.get(network) if network is not None else None
        net.disconnect(container)

    async def remove_network(self, name: str) -> None:
        await asyncio.to_thread(self._remove_network_sync, name)

    def _remove_network_sync(self, name: str) -> None:
        net = self.client.networks.get(name)
        net.remove()

    # In netengine/handlers/docker_handler.py

    async def copy_to_container(self, container_id: str, src_path: str, dest_path: str) -> None:
        """Copy a file from host into a running container."""
        import io
        import tarfile

        # Create a tar stream with the file
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(src_path, arcname=os.path.basename(dest_path))
        tar_stream.seek(0)
        await asyncio.to_thread(self._copy_to_container_sync, container_id, tar_stream, dest_path)

    def _copy_to_container_sync(self, container_id: str, tar_stream: Any, dest_path: str) -> None:
        container = self.client.containers.get(container_id)
        container.put_archive(os.path.dirname(dest_path), tar_stream)

    async def signal_container(self, container_id: str, signal: str) -> None:
        """Send a signal to a container via the Docker daemon (no shell required)."""
        await asyncio.to_thread(self._signal_container_sync, container_id, signal)

    def _signal_container_sync(self, container_id: str, signal: str) -> None:
        container = self.client.containers.get(container_id)
        container.kill(signal=signal)
