"""PostgreSQL connector with asyncpg pool and query builder."""

import os
from typing import Any, Dict, List, Optional

import asyncpg
from loguru import logger

from netengine.connectors.base import Connector


class PostgresConnector(Connector[None]):
    """Manages asyncpg connection pool and provides query builder interface."""

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool[asyncpg.Connection]] = None
        self._db_url = (
            os.getenv(
                "NETENGINE_DB_URL",
                "postgresql://netengine:dev_password@localhost:5432/netengine",
            )
            or os.getenv("DATABASE_URL")
        )

    async def connect(self) -> None:
        """Create asyncpg connection pool."""
        try:
            self._pool = await asyncpg.create_pool(
                self._db_url, min_size=1, max_size=10
            )
            logger.info("PostgreSQL connector connected")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connector disconnected")

    async def health(self) -> bool:
        """Check database connection health."""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.warning(f"PostgreSQL health check failed: {e}")
            return False

    @property
    def pool(self) -> asyncpg.Pool[asyncpg.Connection]:
        """Get underlying connection pool."""
        if not self._pool:
            raise RuntimeError(
                "PostgreSQL connector not connected. Call connect() first."
            )
        return self._pool

    async def query(
        self, sql: str, *args: Any, fetch_one: bool = False
    ) -> Any:
        """Execute a raw SQL query."""
        async with self._pool.acquire() as conn:
            if fetch_one:
                return await conn.fetchrow(sql, *args)
            return await conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        """Execute a statement (INSERT, UPDATE, DELETE, etc.)."""
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def rpc(self, func_name: str, params: Dict[str, Any]) -> Any:
        """Call a PostgreSQL RPC function (stored procedure)."""
        async with self._pool.acquire() as conn:
            return await conn.rpc(func_name, **params)

    async def transaction(self, func: Any) -> Any:
        """Run function within a transaction."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                return await func(conn)
