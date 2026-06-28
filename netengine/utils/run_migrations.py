"""Compatibility wrapper for applying NetEngine database migrations."""

import os
from pathlib import Path
from urllib.parse import quote

from netengine.utils.migration_service import MigrationService


def _db_url_from_environment() -> str:
    db_url = os.environ.get("NETENGINE_DB_URL")
    if db_url:
        return db_url

    db_host = os.environ.get("SUPABASE_DB_HOST", "localhost")
    db_port = os.environ.get("SUPABASE_DB_PORT", "5432")
    db_user = os.environ.get("SUPABASE_DB_USER", "postgres")
    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    db_name = os.environ.get("SUPABASE_DB_NAME", "postgres")
    auth = quote(db_user, safe="")
    if db_password:
        auth = f"{auth}:{quote(db_password, safe='')}"
    return f"postgresql://{auth}@{db_host}:{db_port}/{db_name}"


async def apply_migrations() -> None:
    """Apply SQL migrations to Postgres with explicit partial-failure semantics.

    Reads NETENGINE_DB_URL (e.g. postgresql://user:pass@host:5432/db).
    Falls back to SUPABASE_DB_* variables for backward compatibility with cloud setups.
    """
    migrations_dir = Path(__file__).parent.parent.parent / "migrations"
    service = MigrationService(_db_url_from_environment(), migrations_dir, print)
    await service.apply()


if __name__ == "__main__":
    asyncio.run(apply_migrations())

from netengine.core.migrations import MigrationService, apply_migration_files


async def apply_migrations() -> None:
    """Apply pending SQL migrations using the shared migration service.
import os
from pathlib import Path

from netengine.utils.migrations import apply_migration_files


from __future__ import annotations

    Reads NETENGINE_DB_URL first, then DATABASE_URL for consistency with the
    CLI's startup and migration commands.
    """
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("Database URL is required: set NETENGINE_DB_URL or DATABASE_URL.")

    migrations_dir = Path(__file__).parent.parent.parent / "migrations"
    await MigrationService(db_url, migrations_dir).apply_pending()
    import asyncpg  # type: ignore[import]

    db_url = os.environ.get("NETENGINE_DB_URL")
    migrations_dir = Path(__file__).parent.parent.parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        raise FileNotFoundError(f"No migration files found in: {migrations_dir}")

    if db_url:
        conn = await asyncpg.connect(db_url)
    else:
        parsed_port = int(os.environ.get("SUPABASE_DB_PORT", "5432"))
        conn = await asyncpg.connect(
            host=os.environ.get("SUPABASE_DB_HOST", "localhost"),
            port=parsed_port,
            user=os.environ.get("SUPABASE_DB_USER", "postgres"),
            password=os.environ.get("SUPABASE_DB_PASSWORD", ""),
            database=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        )

    try:
        await apply_migration_files(conn, migration_files)
    finally:
        await conn.close()
import asyncio

from netengine.db.migrations import MigrationRunResult, run_migrations


async def apply_migrations(db_url: str | None = None) -> MigrationRunResult:
    """Apply SQL migrations using the shared migration service."""
    return await run_migrations(db_url)


if __name__ == "__main__":
    result = asyncio.run(apply_migrations())
    for migration in result.results:
        print(f"{migration.status}: {migration.filename}")
    print(
        f"Migrations complete: {result.applied_count} applied, "
        f"{result.skipped_count} skipped, {result.failed_count} failed"
    )
