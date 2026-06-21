# netengines/handlers/docker_handler.py
import asyncio
import docker
from docker.types import IPAMConfig, IPAMPool
from typing import Optional, Dict, List

class DockerHandler:
    def __init__(self):
        self.client = docker.from_env()

    async def ensure_volume(self, name: str) -> None:
        """Create a named volume if it doesn't exist."""
        await asyncio.to_thread(self._ensure_volume_sync, name)

    def _ensure_volume_sync(self, name: str):
        try:
            self.client.volumes.get(name)
        except docker.errors.NotFound:
            self.client.volumes.create(name)

    # In DockerHandler
    async def run_container_one_off(self, image, command, volumes, environment, working_dir=None, **kwargs):
        return await asyncio.to_thread(
            self._run_container_one_off_sync,
            image, command, volumes, environment, working_dir, **kwargs
        )

    def _run_container_one_off_sync(self, image, command, volumes, environment, working_dir, **kwargs):
        container = self.client.containers.run(
            image=image,
            command=command,
            volumes=volumes,
            environment=environment,
            remove=False,
            detach=True,
            working_dir=working_dir,
            **kwargs
        )
        result = container.wait()
        logs = container.logs().decode()
        container.remove()
        return {"exit_code": result["StatusCode"], "logs": logs}

    async def start_container(
        self,
        name: str,
        image: str,
        command: List[str],
        volumes: Dict[str, Dict[str, str]],
        network: str,
        ip: str,
        environment: Dict[str, str],
        **kwargs
    ) -> str:
        """Start a long‑running container attached to a network with a fixed IP."""
        return await asyncio.to_thread(
            self._start_container_sync,
            name, image, command, volumes, network, ip, environment, **kwargs
        )

    def _start_container_sync(self, name, image, command, volumes, network, ip, environment, **kwargs):
        # Ensure network exists (we assume it was created in Phase 0)
        net = self.client.networks.get(network)
        container = self.client.containers.run(
            image=image,
            command=command,
            name=name,
            volumes=volumes,
            environment=environment,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            **kwargs
        )
        # Attach to network with specific IP
        net.connect(container, ipv4_address=ip)
        return container.id

    async def exec_command(self, container_id: str, cmd: List[str]) -> tuple[int, str]:
        """Execute a command inside a running container."""
        return await asyncio.to_thread(self._exec_command_sync, container_id, cmd)

    def _exec_command_sync(self, container_id, cmd):
        container = self.client.containers.get(container_id)
        exec_result = container.exec_run(cmd, demux=True)
        return exec_result.exit_code, (exec_result.output or b"").decode()

    async def stop_container(self, container_id: str) -> None:
        await asyncio.to_thread(self._stop_container_sync, container_id)

    def _stop_container_sync(self, container_id):
        container = self.client.containers.get(container_id)
        container.stop(timeout=10)
        container.remove()