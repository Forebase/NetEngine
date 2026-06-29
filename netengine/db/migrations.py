"""Shared database migration service for NetEngine."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True)
class MigrationResult:
    """Outcome for a single migration file."""

    filename: str
    checksum: str
    status: str
    duration_seconds: float = 0.0
    applied_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class MigrationRunResult:
    """Structured result for a migration run."""

    migrations_dir: Path
    results: tuple[MigrationResult, ...] = field(default_factory=tuple)

    @property
    def applied_count(self) -> int:
        return sum(1 for result in self.results if result.status == "applied")

    @property
    def skipped_count(self) -> int:
        return sum(1 for result in self.results if result.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")


@dataclass(frozen=True)
class MigrationStatusResult:
    """Current database status for a single migration file."""

    filename: str
    checksum: str
    status: str
    applied_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class MigrationStatusReport:
    """Structured status report for migration files."""

    migrations_dir: Path
    results: tuple[MigrationStatusResult, ...] = field(default_factory=tuple)

    @property
    def pending_count(self) -> int:
        return sum(1 for result in self.results if result.status == "pending")

    @property
    def applied_count(self) -> int:
        return sum(1 for result in self.results if result.status == "applied")

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")

    @property
    def drifted_count(self) -> int:
        return sum(1 for result in self.results if result.status == "drifted")


class MigrationChecksumDriftError(RuntimeError):
    """Raised when an already-applied migration file changes on disk."""


_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_seconds DOUBLE PRECISION NOT NULL,
    success BOOLEAN NOT NULL,
    error TEXT
)
"""


def discover_migrations(migrations_dir: Path | str = MIGRATIONS_DIR) -> list[Path]:
    """Discover SQL migration files in lexical order."""
    return sorted(Path(migrations_dir).glob("*.sql"), key=lambda path: path.name)


def migration_checksum(sql: str) -> str:
    """Return a stable checksum for a migration body."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def database_url_from_environment() -> str | None:
    """Build a database URL from NetEngine/Supabase-compatible environment variables."""
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if db_url:
        return db_url

    db_host = os.environ.get("SUPABASE_DB_HOST")
    if not db_host:
        return None

    db_port = os.environ.get("SUPABASE_DB_PORT", "5432")
    db_user = os.environ.get("SUPABASE_DB_USER", "postgres")
    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    db_name = os.environ.get("SUPABASE_DB_NAME", "postgres")
    auth = quote(db_user)
    if db_password:
        auth = f"{auth}:{quote(db_password)}"
    return f"postgresql://{auth}@{db_host}:{db_port}/{quote(db_name)}"


async def run_migrations(
    db_url: str | None = None,
    migrations_dir: Path | str = MIGRATIONS_DIR,
) -> MigrationRunResult:
    """Apply all pending SQL migrations and return structured outcomes."""
    import asyncpg  # type: ignore[import-untyped]

    resolved_dir = Path(migrations_dir)
    migration_files = discover_migrations(resolved_dir)
    if db_url is None:
        db_url = database_url_from_environment()
    if not db_url:
        raise RuntimeError("No database URL configured for migrations")

    conn = await asyncpg.connect(db_url)
    results: list[MigrationResult] = []
    try:
        await conn.execute(_SCHEMA_MIGRATIONS_SQL)
        for migration_path in migration_files:
            sql = migration_path.read_text(encoding="utf-8")
            checksum = migration_checksum(sql)
            filename = migration_path.name
            existing = await conn.fetchrow(
                "SELECT checksum, success FROM schema_migrations WHERE filename = $1",
                filename,
            )
            if existing and existing["success"] and existing["checksum"] == checksum:
                results.append(MigrationResult(filename, checksum, "skipped"))
                continue
            if existing and existing["success"] and existing["checksum"] != checksum:
                raise MigrationChecksumDriftError(
                    f"Checksum drift for migration {filename}: "
                    f"recorded {existing['checksum']}, current {checksum}"
                )

            start = time.perf_counter()
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    duration = time.perf_counter() - start
                    await conn.execute(
                        """
                        INSERT INTO schema_migrations
                            (filename, checksum, applied_at, duration_seconds, success, error)
                        VALUES ($1, $2, NOW(), $3, TRUE, NULL)
                        ON CONFLICT (filename) DO UPDATE SET
                            checksum = EXCLUDED.checksum,
                            applied_at = EXCLUDED.applied_at,
                            duration_seconds = EXCLUDED.duration_seconds,
                            success = TRUE,
                            error = NULL
                        """,
                        filename,
                        checksum,
                        duration,
                    )
                results.append(
                    MigrationResult(
                        filename,
                        checksum,
                        "applied",
                        duration_seconds=duration,
                        applied_at=datetime.now(UTC),
                    )
                )
            except Exception as exc:
                duration = time.perf_counter() - start
                error = str(exc)
                await conn.execute(
                    """
                    INSERT INTO schema_migrations
                        (filename, checksum, applied_at, duration_seconds, success, error)
                    VALUES ($1, $2, NOW(), $3, FALSE, $4)
                    ON CONFLICT (filename) DO UPDATE SET
                        checksum = EXCLUDED.checksum,
                        applied_at = EXCLUDED.applied_at,
                        duration_seconds = EXCLUDED.duration_seconds,
                        success = FALSE,
                        error = EXCLUDED.error
                    """,
                    filename,
                    checksum,
                    duration,
                    error,
                )
                results.append(
                    MigrationResult(
                        filename,
                        checksum,
                        "failed",
                        duration_seconds=duration,
                        applied_at=datetime.now(UTC),
                        error=error,
                    )
                )
                raise RuntimeError(f"Migration {filename} failed: {error}") from exc
    finally:
        await conn.close()

    return MigrationRunResult(resolved_dir, tuple(results))


async def migration_status(
    db_url: str | None = None,
    migrations_dir: Path | str = MIGRATIONS_DIR,
) -> MigrationStatusReport:
    """Inspect migration state without applying pending migration files."""
    import asyncpg

    resolved_dir = Path(migrations_dir)
    migration_files = discover_migrations(resolved_dir)
    if db_url is None:
        db_url = database_url_from_environment()
    if not db_url:
        raise RuntimeError("No database URL configured for migrations")

    conn = await asyncpg.connect(db_url)
    results: list[MigrationStatusResult] = []
    try:
        await conn.execute(_SCHEMA_MIGRATIONS_SQL)
        for migration_path in migration_files:
            sql = migration_path.read_text(encoding="utf-8")
            checksum = migration_checksum(sql)
            filename = migration_path.name
            existing = await conn.fetchrow(
                "SELECT checksum, success, applied_at, error FROM schema_migrations WHERE filename = $1",
                filename,
            )
            if not existing:
                status = "pending"
            elif existing["success"] and existing["checksum"] == checksum:
                status = "applied"
            elif existing["success"]:
                status = "drifted"
            else:
                status = "failed"
            results.append(
                MigrationStatusResult(
                    filename=filename,
                    checksum=checksum,
                    status=status,
                    applied_at=existing["applied_at"] if existing else None,
                    error=existing["error"] if existing else None,
                )
            )
    finally:
        await conn.close()

    return MigrationStatusReport(resolved_dir, tuple(results))
