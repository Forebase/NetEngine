import secrets
import tempfile
from datetime import UTC, datetime

from netengine.handlers.dns import DNSHandler
from netengine.handlers.docker_handler import DockerHandler
from netengine.handlers.pki_handler import PKIHandler


class StorageHandler:
    def __init__(self, context, docker: DockerHandler, dns: DNSHandler, pki: PKIHandler, state):
        self.context = context
        self.docker = docker
        self.dns = dns
        self.pki = pki
        self.state = state
        self.container_name = "netengines_minio"
        self.storage_ip = context.spec.world_services.storage.listen_ip
        self.storage_dns = context.spec.world_services.storage.canonical_name

    async def deploy_minio(self) -> dict:
        """Start MinIO container with TLS and create platform bucket."""
        # 1. Issue cert for storage.platform.internal
        cert, key = await self.pki.issue_cert(self.storage_dns, [])

        # Track issued certificate in RuntimeState
        expiry = self.pki.extract_cert_expiry(cert)
        self.state.issued_certificates[self.storage_dns] = {
            "cert_type": "storage",
            "issued_at": datetime.now(UTC).isoformat(),
            "expires_at": expiry.isoformat(),
            "sans": [],
            "rotated_at": None,
            "version": 1,
        }
        self.state.save()

        # Write cert and key to a temporary directory (cleaned up by OS)
        cert_dir = tempfile.mkdtemp(prefix="netengines_minio_certs_")
        with open(f"{cert_dir}/public.crt", "w") as f:
            f.write(cert)
        with open(f"{cert_dir}/private.key", "w") as f:
            f.write(key)

        # 2. Generate access credentials
        access_key = secrets.token_urlsafe(16)
        secret_key = secrets.token_urlsafe(32)

        # 3. Start MinIO container
        await self.docker.start_container(
            name=self.container_name,
            image="minio/minio:latest",
            command=["server", "/data", "--console-address", ":9001"],
            volumes={cert_dir: {"bind": "/root/.minio/certs", "mode": "ro"}},
            network="core",
            ip=self.storage_ip,
            environment={
                "MINIO_ROOT_USER": access_key,
                "MINIO_ROOT_PASSWORD": secret_key,
                "MINIO_CERT_FILE": "/root/.minio/certs/public.crt",
                "MINIO_KEY_FILE": "/root/.minio/certs/private.key",
            },
        )
        # 4. Register DNS
        await self.dns.add_zone_record(
            self.context, "platform.internal", "A", "storage", self.storage_ip, 300
        )
        # 5. Create platform bucket (via API or mc)
        # We'll use `mc` CLI inside a one‑off container.
        await self._create_bucket("platform", access_key, secret_key)
        # 6. Store credentials in state
        self.state.minio_access_key = access_key
        self.state.minio_secret_key = secret_key
        self.state.storage_deployed = True
        self.state.save()

        return {
            "container_name": self.container_name,
            "ip": self.storage_ip,
            "dns": self.storage_dns,
            "access_key": access_key,
            "bucket": "platform",
        }

    async def _create_bucket(self, bucket_name: str, access_key: str, secret_key: str) -> None:
        """Use `mc` to create a bucket."""
        # One‑off container using minio/mc
        cmd = [
            "sh",
            "-c",
            f"mc alias set myminio https://{self.storage_ip} {access_key} {secret_key} --insecure && "
            f"mc mb myminio/{bucket_name} --insecure",
        ]
        await self.docker.run_container_one_off(
            image="minio/mc:latest", command=cmd, volumes={}, environment={}
        )
