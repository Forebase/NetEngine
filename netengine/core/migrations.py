"""Database migration service for NetEngine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from netengine.events.queues import Queue


@dataclass(frozen=True)
class MigrationFile:
    """A migration discovered on disk."""

    version: str
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class MigrationRecord:
    """A migration record stored in the database."""

    version: str
    name: str
    checksum: str | None
    status: str
    applied_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class MigrationStatus:
    """Complete migration and pgmq prerequisite status."""

    applied: list[MigrationRecord]
    pending: list[MigrationFile]
    failed: list[MigrationRecord]
    checksum_drifted: list[tuple[MigrationFile, MigrationRecord]]
    pgmq_available: bool
    pgmq_installed: bool
    missing_queues: list[str]

    @property
    def ok(self) -> bool:
        return (
            not self.pending
            and not self.failed
            and not self.checksum_drifted
            and self.pgmq_available
            and self.pgmq_installed
            and not self.missing_queues
        )


class MigrationService:
    """Apply and inspect SQL migrations against Postgres."""

    def __init__(self, db_url: str, migrations_dir: Path) -> None:
        self.db_url = db_url
        self.migrations_dir = migrations_dir

    def discover(self) -> list[MigrationFile]:
        migrations: list[MigrationFile] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            version, _, rest = path.stem.partition("_")
            name = rest or path.stem
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            migrations.append(
                MigrationFile(version=version, name=name, path=path, checksum=checksum)
            )
        return migrations

    async def apply_pending(self) -> list[MigrationRecord]:
        """Apply pending migrations and return records applied in this invocation."""
        import asyncpg  # type: ignore[import-untyped]

        conn = await asyncpg.connect(self.db_url)
        try:
            await self._ensure_table(conn)
            records = await self._records(conn)
            by_version = {record.version: record for record in records}
            applied: list[MigrationRecord] = []

            for migration in self.discover():
                existing = by_version.get(migration.version)
                if existing and existing.status == "applied":
                    continue

                sql = migration.path.read_text()
                try:
                    await conn.execute(sql)
                except Exception as exc:
                    error = str(exc)
                    await self._upsert_record(conn, migration, "failed", error)
                    raise RuntimeError(f"Migration {migration.path.name} failed: {error}") from exc

                await self._upsert_record(conn, migration, "applied", None)
                record = MigrationRecord(
                    migration.version,
                    migration.name,
                    migration.checksum,
                    "applied",
                )
                applied.append(record)

            return applied
        finally:
            await conn.close()

    async def status(self) -> MigrationStatus:
        import asyncpg

        conn = await asyncpg.connect(self.db_url)
        try:
            await self._ensure_table(conn)
            records = await self._records(conn)
            by_version = {record.version: record for record in records}
            files = self.discover()

            pending = [m for m in files if by_version.get(m.version) is None]
            failed = [record for record in records if record.status == "failed"]
            drifted = [
                (migration, by_version[migration.version])
                for migration in files
                if by_version.get(migration.version)
                and by_version[migration.version].status == "applied"
                and by_version[migration.version].checksum != migration.checksum
            ]

            pgmq_available = await self._pgmq_available(conn)
            pgmq_installed = await self._pgmq_installed(conn)
            missing_queues = (
                await self._missing_queues(conn) if pgmq_installed else [q.value for q in Queue]
            )

            return MigrationStatus(
                applied=[record for record in records if record.status == "applied"],
                pending=pending,
                failed=failed,
                checksum_drifted=drifted,
                pgmq_available=pgmq_available,
                pgmq_installed=pgmq_installed,
                missing_queues=missing_queues,
            )
        finally:
            await conn.close()

    async def _ensure_table(self, conn: Any) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS netengine_schema_migrations (
                version text PRIMARY KEY,
                name text NOT NULL,
                checksum text,
                status text NOT NULL CHECK (status IN ('applied', 'failed')),
                applied_at timestamptz,
                error text
            )
            """
        )

    async def _records(self, conn: Any) -> list[MigrationRecord]:
        rows = await conn.fetch(
            """
            SELECT version, name, checksum, status, applied_at, error
            FROM netengine_schema_migrations
            ORDER BY version
            """
        )
        return [
            MigrationRecord(
                version=row["version"],
                name=row["name"],
                checksum=row["checksum"],
                status=row["status"],
                applied_at=row["applied_at"],
                error=row["error"],
            )
            for row in rows
        ]

    async def _upsert_record(
        self, conn: Any, migration: MigrationFile, status: str, error: str | None
    ) -> None:
        await conn.execute(
            """
            INSERT INTO netengine_schema_migrations
                (version, name, checksum, status, applied_at, error)
            VALUES ($1, $2, $3, $4, CASE WHEN $4 = 'applied' THEN now() ELSE NULL END, $5)
            ON CONFLICT (version) DO UPDATE SET
                name = EXCLUDED.name,
                checksum = EXCLUDED.checksum,
                status = EXCLUDED.status,
                applied_at = EXCLUDED.applied_at,
                error = EXCLUDED.error
            """,
            migration.version,
            migration.name,
            migration.checksum,
            status,
            error,
        )

    async def _pgmq_available(self, conn: Any) -> bool:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pgmq')"
            )
        )

    async def _pgmq_installed(self, conn: Any) -> bool:
        return bool(
            await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgmq')")
        )

    async def _missing_queues(self, conn: Any) -> list[str]:
        rows = await conn.fetch(
            """
            SELECT q.queue_name
            FROM unnest($1::text[]) AS q(queue_name)
            WHERE to_regclass('pgmq.q_' || q.queue_name) IS NULL
            ORDER BY q.queue_name
            """,
            [queue.value for queue in Queue],
        )
        return [row["queue_name"] for row in rows]
