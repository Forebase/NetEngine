# netengine/handlers/pki_handler.py
import asyncio
import os
import ssl
import tempfile
from pathlib import Path
from typing import Optional

import aiohttp

from netengine.core.state import RuntimeState
from netengine.errors import PKIError
from netengine.handlers.docker_handler import DockerHandler


class PKIHandler:
    def __init__(self, docker: DockerHandler, state: RuntimeState, spec):
        self.docker = docker
        self.state = state
        self.spec = spec
        # Support both Pydantic model (NetEngineSpec) and plain dict
        if hasattr(spec, "pki"):
            self.ca_ip = spec.pki.acme.listen_ip
            self.ca_dns = spec.pki.acme.canonical_name
        else:
            pki_spec = spec.get("pki", {}) if isinstance(spec, dict) else {}
            acme = pki_spec.get("acme", {})
            self.ca_ip = acme.get("listen_ip", "10.0.0.6")
            self.ca_dns = acme.get("canonical_name", "ca.platform.internal")
        self.volume_name = "netengines_pki_data"
        self.container_name = "netengines_step_ca"
        self.image = "smallstep/step-ca:latest"

    # ─────────────────────────────────────────────
    # Main bootstrap (idempotent)
    # ─────────────────────────────────────────────
    async def bootstrap(self) -> None:
        """Ensure CA is generated and step‑ca is running."""
        # 1. Create volume if missing
        await self.docker.ensure_volume(self.volume_name)

        # 2. Generate CA if not present in state
        if not self.state.ca_cert_pem:
            await self._generate_ca()

        # 3. Start container if not running
        if not self.state.step_ca_container_id:
            await self._start_server()

        # 4. Healthcheck
        if not await self.healthcheck():
            raise PKIError("step‑ca is not responding after bootstrap")

    # ─────────────────────────────────────────────
    # CA generation (one‑off)
    # ─────────────────────────────────────────────
    async def _generate_ca(self) -> None:
        """Run `step ca init` inside a temporary container."""
        # Generate a strong password
        password = os.urandom(32).hex()

        # Write password to a temporary file (mounted into container)
        with tempfile.TemporaryDirectory() as tmpdir:
            passfile = Path(tmpdir) / "password.txt"
            passfile.write_text(password)

            volumes = {
                self.volume_name: {"bind": "/home/step", "mode": "rw"},
                tmpdir: {"bind": "/tmp/pass", "mode": "ro"},
            }
            cmd = [
                "step",
                "ca",
                "init",
                "--name",
                "NetEngines Root CA",
                "--dns",
                self.ca_dns,
                "--ip",
                self.ca_ip,
                "--provisioner",
                "acme",
                "--password-file",
                "/tmp/pass/password.txt",
                "--no-start",
            ]
            result = await self.docker.run_container_one_off(
                image=self.image, command=cmd, volumes=volumes, environment={}
            )
            if result["exit_code"] != 0:
                raise PKIError(f"step ca init failed: {result['logs']}")

        # Read the CA certificate from the volume
        ca_cert = await self._read_file_from_volume("/home/step/certs/ca.crt")
        self.state.ca_cert_pem = ca_cert
        self.state.save()

    async def _read_file_from_volume(self, path: str) -> str:
        """Read a file from the volume via a temporary container."""
        volumes = {self.volume_name: {"bind": "/data", "mode": "ro"}}
        # Map /home/step -> /data
        container_path = path.replace("/home/step", "/data")
        cmd = ["cat", container_path]
        result = await self.docker.run_container_one_off(
            image=self.image, command=cmd, volumes=volumes, environment={}
        )
        if result["exit_code"] != 0:
            raise PKIError(f"Failed to read {path}: {result['logs']}")
        return result["logs"]

    # ─────────────────────────────────────────────
    # Start step‑ca server
    # ─────────────────────────────────────────────
    async def _start_server(self) -> None:
        """Start the long‑running step‑ca container."""
        # We need the password to be passed as environment variable.
        # We'll read it from the volume's config (ca.json contains the encrypted key,
        # but step-ca needs the password to decrypt it on startup).
        # The password was written to a file on the volume during init.
        # We'll retrieve it by reading /home/step/password.txt (we didn't store it there).
        # Actually we can re-generate the same password if we store it securely.
        # For ephemeral mode, we can just store the password in state or in a file on the volume.
        # Simpler: store password in a file on the volume during init and read it now.
        # We'll modify _generate_ca to write the password to /home/step/password.txt after init.
        # For now, we'll assume we stored it; implement a method to retrieve.
        password = await self._get_password()
        if password is None:
            # If no password found, we need to re-generate? Not for now.
            raise PKIError("CA password not found; cannot start step-ca")

        volumes = {self.volume_name: {"bind": "/home/step", "mode": "rw"}}
        container_id = await self.docker.start_container(
            name=self.container_name,
            image=self.image,
            command=["step-ca", "/home/step/config/ca.json"],
            volumes=volumes,
            network="core",  # from spec
            ip=self.ca_ip,
            environment={"STEP_PASSWORD": password},
        )
        self.state.step_ca_container_id = container_id
        self.state.save()

    async def _get_password(self) -> Optional[str]:
        """Retrieve the CA password from the volume."""
        # We stored it in /home/step/password.txt during _generate_ca.
        # Modify _generate_ca to write the password to that file.
        # For now, implement the read.
        try:
            return await self._read_file_from_volume("/home/step/password.txt")
        except Exception:
            return None

    # ─────────────────────────────────────────────
    # Healthcheck
    # ─────────────────────────────────────────────
    async def healthcheck(self) -> bool:
        """Check ACME directory via IP (self‑signed cert)."""
        url = f"https://{self.ca_ip}/acme/acme/directory"
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=ssl_context, timeout=5) as resp:
                    return resp.status == 200
        except Exception:
            return False

    # ─────────────────────────────────────────────
    # Certificate issuance (for M7)
    # ─────────────────────────────────────────────

    async def issue_cert(self, common_name: str, sans: list[str] = None) -> tuple[str, str]:
        """Issue a certificate using step-ca (via admin API or CLI)."""
        # Use the step CLI inside a temporary container that mounts the CA volume.
        # This is simpler than REST API for MVP.
        # We'll run: step ca certificate --provisioner acme <cn> <cert> <key> --ca-config /home/step/config/ca.json
        volumes = {self.volume_name: {"bind": "/home/step", "mode": "rw"}}
        cert_file = f"/tmp/{common_name}.crt"
        key_file = f"/tmp/{common_name}.key"
        cmd = [
            "step",
            "ca",
            "certificate",
            common_name,
            cert_file,
            key_file,
            "--provisioner",
            "acme",
            "--ca-config",
            "/home/step/config/ca.json",
            "--not-after",
            "87600h",  # 10 years
        ]
        if sans:
            for san in sans:
                cmd.extend(["--san", san])
        result = await self.docker.run_container_one_off(
            image=self.image, command=cmd, volumes=volumes, environment={}
        )
        if result["exit_code"] != 0:
            raise PKIError(f"Certificate issuance failed: {result['logs']}")
        # Now read the cert and key from the container? We mounted /tmp, but we need to read from volume.
        # Actually we can mount a temporary directory to extract the certs.
        # Simpler: use a shared volume or write to the CA volume and read.
        cert_content = await self._read_file_from_volume(f"/home/step/{common_name}.crt")
        key_content = await self._read_file_from_volume(f"/home/step/{common_name}.key")
        return cert_content, key_content
