"""Database migration helpers."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from types import TracebackType
from typing import Protocol

from netengine.logs import get_logger

logger = get_logger(__name__)


class AsyncTransaction(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class AsyncMigrationConnection(Protocol):
    async def execute(self, query: str, *args: object) -> object: ...

    async def fetchval(self, query: str, *args: object) -> object: ...

    def transaction(self) -> AsyncTransaction: ...


MIGRATION_LEDGER_TABLE = "netengine_migration_ledger"
MIGRATION_LEDGER_SQL = f"""
CREATE TABLE IF NOT EXISTS {MIGRATION_LEDGER_TABLE} (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms INTEGER,
    success BOOLEAN NOT NULL,
    error TEXT
);
"""


def migration_checksum(sql: str) -> str:
    """Return the SHA-256 checksum for a migration body."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


async def apply_migration_files(conn: AsyncMigrationConnection, migration_files: list[Path]) -> int:
    """Apply migration files through an asyncpg connection.

    Creates the bootstrap migration ledger before applying files. Already-applied
    migrations are skipped when their checksums match. If an applied migration's
    contents changed, abort before executing it.
    """
    await conn.execute(MIGRATION_LEDGER_SQL)

    applied_count = 0
    for migration_path in migration_files:
        sql = migration_path.read_text()
        checksum = migration_checksum(sql)
        filename = migration_path.name
        existing_checksum = await conn.fetchval(
            f"SELECT checksum FROM {MIGRATION_LEDGER_TABLE} WHERE filename = $1",
            filename,
        )

        if existing_checksum is not None:
            if existing_checksum != checksum:
                raise RuntimeError(
                    "Applied migration changed: "
                    f"{filename} has checksum {checksum}, but the ledger contains "
                    f"{existing_checksum}. Create a new migration instead of editing "
                    "an applied migration."
                )
            logger.info(f"Skipping already-applied migration: {filename}")
            continue

        logger.info(f"Running migration: {filename}")
        started = time.perf_counter()
        async with conn.transaction():
            await conn.execute(sql)
            duration_ms = round((time.perf_counter() - started) * 1000)
            await conn.execute(
                f"""
                INSERT INTO {MIGRATION_LEDGER_TABLE}
                    (filename, checksum, duration_ms, success)
                VALUES ($1, $2, $3, TRUE)
                """,
                filename,
                checksum,
                duration_ms,
            )
        applied_count += 1

    return applied_count
