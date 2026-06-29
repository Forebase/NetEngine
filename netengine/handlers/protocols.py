"""Shared protocols for handler dependencies."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DockerAdapterProtocol(Protocol):
    """Async Docker operations consumed by NetEngine handlers."""

    client: Any
    containers: Any

    async def ensure_volume(self, name: str) -> None: ...

    async def run_container_one_off(
        self,
        image: str,
        command: list[str] | str,
        volumes: dict[str, Any],
        environment: dict[str, str] | None,
        working_dir: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

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
    ) -> str: ...

    async def exec_command(self, container_id: str, cmd: list[str]) -> tuple[int, str]: ...

    async def stop_container(self, container_id: str) -> None: ...

    async def create_network(
        self,
        name: str,
        driver: str = "bridge",
        subnet: str | None = None,
        internal: bool = False,
    ) -> None: ...

    async def connect_network(self, container: str, network: str, ip: str | None) -> None: ...

    async def disconnect_network(self, container: str, network: str) -> None: ...

    async def remove_network(self, name: str) -> None: ...

    async def copy_to_container(self, container_id: str, src_path: str, dest_path: str) -> None: ...

    async def signal_container(self, container_id: str, signal: str) -> None: ...
