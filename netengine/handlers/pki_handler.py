# netengine/handlers/pki_handler.py
import asyncio
import os
import ssl
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

from netengine.core.state import RuntimeState
from netengine.errors import PKIError
from netengine.handlers.docker_handler import DockerHandler
from netengine.logging import get_logger

logger = get_logger(__name__)


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
    def _pki_flag(self, flag: str) -> bool:
        """Return a boolean PKI spec flag, handling both model and dict specs."""
        if hasattr(self.spec, "pki"):
            return bool(getattr(self.spec.pki, flag, False))
        if isinstance(self.spec, dict):
            return bool(self.spec.get("pki", {}).get(flag, False))
        return False

    async def bootstrap(self) -> None:
        """Ensure CA is generated and step‑ca is running."""
        # 1. Create volume if missing
        await self.docker.ensure_volume(self.volume_name)

        # 2. Generate CA if not present in state
        if not self.state.ca_cert_pem:
            await self._generate_ca()
            # Inject optional features into ca.json before server starts
            if self._pki_flag("crl_enabled"):
                await self._inject_crl_config()
            if self._pki_flag("ocsp_enabled"):
                await self._inject_ocsp_config()

        # 3. Start container if not running
        if not self.state.step_ca_container_id:
            await self._start_server()

        # 4. Healthcheck
        if not await self.healthcheck():
            raise PKIError("step‑ca is not responding after bootstrap")

        # 5. Read intermediate CA cert when enabled
        if self._pki_flag("intermediate_ca_enabled") and not self.state.intermediate_ca_cert:
            self.state.intermediate_ca_cert = await self.read_intermediate_cert()
            self.state.save()

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

            # Persist password to volume so _start_server can retrieve it
            persist_result = await self.docker.run_container_one_off(
                image=self.image,
                command=["cp", "/tmp/pass/password.txt", "/home/step/password.txt"],
                volumes=volumes,
                environment={},
            )
            if persist_result["exit_code"] != 0:
                raise RuntimeError(
                    f"Failed to persist CA password to volume: {persist_result['logs']}"
                )

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
        except Exception as exc:
            logger.debug(f"CA password not readable from volume: {exc}")
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
        except Exception as exc:
            logger.debug(f"PKI healthcheck failed ({url}): {exc}")
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
        # Write certs to volume path so _read_file_from_volume can retrieve them
        cert_file = f"/home/step/{common_name}.crt"
        key_file = f"/home/step/{common_name}.key"
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

    # ─────────────────────────────────────────────
    # Intermediate CA
    # ─────────────────────────────────────────────

    async def read_intermediate_cert(self) -> str:
        """Read the intermediate CA certificate from the step-ca volume.

        step-ca generates a root + intermediate CA pair by default; the
        intermediate is what signs leaf certs.  When intermediate_ca_enabled
        is True the caller should store and distribute this cert separately.
        """
        try:
            return await self._read_file_from_volume("/home/step/certs/intermediate_ca.crt")
        except Exception as exc:
            raise PKIError(f"Failed to read intermediate CA certificate: {exc}") from exc

    # ─────────────────────────────────────────────
    # CRL and OCSP
    # ─────────────────────────────────────────────

    async def _read_ca_config(self) -> dict:
        """Read and parse the step-ca ca.json from the PKI volume."""
        import json as _json

        volumes = {self.volume_name: {"bind": "/home/step", "mode": "ro"}}
        result = await self.docker.run_container_one_off(
            image=self.image,
            command=["cat", "/home/step/config/ca.json"],
            volumes=volumes,
            environment={},
        )
        if result["exit_code"] != 0:
            raise PKIError(f"Failed to read CA config: {result['logs']}")
        return _json.loads(result["logs"])

    async def _write_ca_config(self, config: dict) -> None:
        """Write an updated ca.json back to the step-ca volume."""
        import json as _json

        updated = _json.dumps(config, indent=2)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(updated)
            tmp_path = f.name
        try:
            updated_volumes = {
                self.volume_name: {"bind": "/home/step", "mode": "rw"},
                tmp_path: {"bind": "/tmp/ca_updated.json", "mode": "ro"},
            }
            result = await self.docker.run_container_one_off(
                image=self.image,
                command=["cp", "/tmp/ca_updated.json", "/home/step/config/ca.json"],
                volumes=updated_volumes,
                environment={},
            )
            if result["exit_code"] != 0:
                raise PKIError(f"Failed to write CA config: {result['logs']}")
        finally:
            os.unlink(tmp_path)

    async def _inject_crl_config(self) -> None:
        """Add CRL generation config to ca.json (called before server start)."""
        config = await self._read_ca_config()
        config["crl"] = {
            "enabled": True,
            "cacheDuration": "3h",
            "renewalDisabled": False,
            "cacheDisabled": False,
        }
        await self._write_ca_config(config)
        logger.info("CRL enabled in step-ca configuration")

    async def _inject_ocsp_config(self) -> None:
        """Add OCSP responder config to ca.json (called before server start)."""
        config = await self._read_ca_config()
        authority = config.setdefault("authority", {})
        authority.setdefault("claims", {})["enableOCSP"] = True
        await self._write_ca_config(config)
        logger.info("OCSP responder enabled in step-ca configuration")

    # ─────────────────────────────────────────────
    # DNSSEC
    # ─────────────────────────────────────────────

    async def setup_dnssec(
        self,
        zone: str,
        ksk_lifetime_days: int = 365,
        zsk_lifetime_days: int = 30,
    ) -> dict:
        """Generate DNSSEC KSK and ZSK keys for *zone* using BIND's dnssec-keygen.

        Keys are stored in the ``netengines_dnssec_keys`` Docker volume so that
        CoreDNS can mount them and activate the ``dnssec`` plugin.  Returns a
        dict of key metadata that the caller should persist in state for later
        rotation.
        """
        dnssec_volume = "netengines_dnssec_keys"
        await self.docker.ensure_volume(dnssec_volume)

        bind_image = "internetsystemsconsortium/bind9:9.18"
        volumes = {dnssec_volume: {"bind": "/keys", "mode": "rw"}}

        # KSK — signs only the DNSKEY RRset
        ksk_result = await self.docker.run_container_one_off(
            image=bind_image,
            command=[
                "dnssec-keygen",
                "-f", "KSK",
                "-a", "ECDSAP256SHA256",
                "-n", "ZONE",
                zone,
            ],
            volumes=volumes,
            environment={},
            working_dir="/keys",
        )
        if ksk_result["exit_code"] != 0:
            raise PKIError(
                f"DNSSEC KSK generation failed for zone '{zone}': {ksk_result['logs']}"
            )
        ksk_name = ksk_result["logs"].strip()

        # ZSK — signs all other RRsets
        zsk_result = await self.docker.run_container_one_off(
            image=bind_image,
            command=[
                "dnssec-keygen",
                "-a", "ECDSAP256SHA256",
                "-n", "ZONE",
                zone,
            ],
            volumes=volumes,
            environment={},
            working_dir="/keys",
        )
        if zsk_result["exit_code"] != 0:
            raise PKIError(
                f"DNSSEC ZSK generation failed for zone '{zone}': {zsk_result['logs']}"
            )
        zsk_name = zsk_result["logs"].strip()

        return {
            "zone": zone,
            "ksk_name": ksk_name,
            "zsk_name": zsk_name,
            "volume": dnssec_volume,
            "algorithm": "ECDSAP256SHA256",
            "ksk_lifetime_days": ksk_lifetime_days,
            "zsk_lifetime_days": zsk_lifetime_days,
        }

    def extract_cert_expiry(self, cert_pem: str) -> datetime:
        """Extract the notAfter date from a PEM certificate."""
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend

            der = ssl.PEM_cert_to_DER_cert(cert_pem)
            cert = x509.load_der_x509_certificate(der, default_backend())
            return cert.not_valid_after_utc
        except ImportError:
            pass
        except Exception as exc:
            logger.debug(f"Failed to parse cert with cryptography: {exc}")

        # Fallback: parse using openssl command
        try:
            result = subprocess.run(
                ["openssl", "x509", "-noout", "-dates"],
                input=cert_pem.encode(),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("notAfter="):
                        date_str = line.split("=", 1)[1]
                        return datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
        except Exception as exc:
            logger.debug(f"Failed to parse cert with openssl: {exc}")

        raise PKIError(f"Unable to extract expiry date from certificate")
