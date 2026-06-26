"""Connector registry and factory functions."""

from typing import Dict, Optional

from loguru import logger

from netengine.connectors.base import Connector
from netengine.connectors.docker_connector import DockerConnector
from netengine.connectors.keycloak_connector import KeycloakConnector
from netengine.connectors.postgres_connector import PostgresConnector
from netengine.connectors.pgmq_connector import PGMQConnector

_connectors: Dict[str, Connector] = {}


async def get_docker_connector() -> DockerConnector:
    """Get or create Docker connector."""
    if "docker" not in _connectors:
        _connectors["docker"] = DockerConnector()
        await _connectors["docker"].connect()
    return _connectors["docker"]


async def get_postgres_connector() -> PostgresConnector:
    """Get or create PostgreSQL connector."""
    if "postgres" not in _connectors:
        _connectors["postgres"] = PostgresConnector()
        await _connectors["postgres"].connect()
    return _connectors["postgres"]


async def get_pgmq_connector() -> PGMQConnector:
    """Get or create PGMQ connector."""
    if "pgmq" not in _connectors:
        postgres = await get_postgres_connector()
        _connectors["pgmq"] = PGMQConnector(postgres)
        await _connectors["pgmq"].connect()
    return _connectors["pgmq"]


async def get_keycloak_connector(
    keycloak_url: str, admin_username: str, admin_password: str
) -> KeycloakConnector:
    """Get or create Keycloak connector for specific realm."""
    key = f"keycloak_{keycloak_url}_{admin_username}"
    if key not in _connectors:
        _connectors[key] = KeycloakConnector(keycloak_url, admin_username, admin_password)
        await _connectors[key].connect()
    return _connectors[key]


async def disconnect_all() -> None:
    """Disconnect all connectors on shutdown."""
    for name, connector in _connectors.items():
        try:
            await connector.disconnect()
            logger.info(f"Disconnected {name}")
        except Exception as e:
            logger.warning(f"Error disconnecting {name}: {e}")
    _connectors.clear()


__all__ = [
    "Connector",
    "DockerConnector",
    "PostgresConnector",
    "KeycloakConnector",
    "PGMQConnector",
    "get_docker_connector",
    "get_postgres_connector",
    "get_pgmq_connector",
    "get_keycloak_connector",
    "disconnect_all",
]
