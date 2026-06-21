import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

from netengine.handlers.docker_handler import DockerHandler
from netengine.core.state import RuntimeState

class PKIHandler:
    def __init__(self, docker: DockerHandler, state: RuntimeState):
        self.docker = docker
        self.state = state
        self.volume_name = "netengines_pki_data"
        self.ca_ip = "10.0.0.6"          # from spec pki.root_ca.acme.listen_ip
        self.ca_dns = "ca.platform.internal"

    async def generate_root_ca(self) -> str:
        """Run step ca init in a one-off container to create CA key and config."""
        # 1. Create a Docker volume if not exists
        await self.docker.ensure_volume(self.volume_name)

        # 2. Generate a random password for step-ca (store in volume)
        password = os.urandom(16).hex()
        # write password to a file on the volume? We'll use environment variable.

        # 3. Run step ca init
        cmd = [
            "step", "ca", "init",
            "--name", "NetEngines Root CA",
            "--dns", self.ca_dns,
            "--ip", self.ca_ip,
            "--provisioner", "acme",
            "--password-file", "/tmp/password.txt",
            "--no-start"  # do not start the server
        ]
        # We'll pass the password as an env var and write it inside the container.
        # Simpler: use --password-file with a mounted file.
        # We'll create a temporary file with the password and mount it.

        # Actually, step ca init creates ca.json, certs, and keys in the current dir.
        # We'll mount the volume to /home/step and run in that directory.

        # Use docker_handler.run_container with step-ca image:
        await self.docker.run_container(
            image="smallstep/step-ca:latest",
            command=cmd,
            volumes={self.volume_name: "/home/step"},
            environment={"STEP_PASSWORD": password},
            # The container will exit after init
        )

        # 4. Store the CA certificate in runtime_state for later use
        ca_cert = await self._read_ca_cert_from_volume()
        self.state.ca_cert_pem = ca_cert
        await self.state.save()
        return ca_cert

    async def start_ca_server(self):
        """Start the long‑running step-ca container."""
        # The volume now contains ca.json and the keys.
        # Start the container with the proper command.
        container_name = "netengines_step_ca"
        await self.docker.start_container(
            name=container_name,
            image="smallstep/step-ca:latest",
            command=["step-ca", "/home/step/config/ca.json"],
            volumes={self.volume_name: "/home/step"},
            network="core",  # hardcoded core network
            ip=self.ca_ip,
            ports={},  # no host ports
            environment={"STEP_PASSWORD": self._get_password()},  # retrieve from volume/env
        )

    async def healthcheck(self) -> bool:
        """Check ACME directory is reachable via IP."""
        # Use aiohttp to GET https://10.0.0.6/acme/acme/directory
        # ignore SSL verification (cert is self-signed)
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://{self.ca_ip}/acme/acme/directory", ssl=False) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def issue_cert(self, common_name: str, sans: list[str]) -> tuple[str, str]:
        """Use step-ca admin API to issue a cert (for future use)."""
        # We'll implement this later (M7). For M2, only the CA is up.
        pass

    async def _read_ca_cert_from_volume(self) -> str:
        # Use docker cp or volume inspection to read /home/step/certs/ca.crt
        pass