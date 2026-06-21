import docker
from docker.types import IPAMConfig, IPAMPool

async def ensure_volume(self, name):
    client = docker.from_env()
    try:
        client.volumes.get(name)
    except docker.errors.NotFound:
        client.volumes.create(name)

async def run_container(self, image, command, volumes, environment, **kwargs):
    client = docker.from_env()
    container = client.containers.run(
        image=image,
        command=command,
        volumes=volumes,
        environment=environment,
        remove=True,  # clean up after exit
        **kwargs
    )
    # Wait for completion and check exit code
    result = container.wait()
    if result["StatusCode"] != 0:
        logs = container.logs().decode()
        raise RuntimeError(f"Container failed: {logs}")
    return container

async def start_container(self, name, image, command, volumes, network, ip, environment):
    client = docker.from_env()
    # Ensure network exists (phase 0 already created core)
    # Attach with specific IP
    container = client.containers.run(
        image=image,
        command=command,
        name=name,
        volumes=volumes,
        environment=environment,
        network=network,
        ip=ip,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
    )
    return container