import os
from pathlib import Path

from netengine.utils.migrations import apply_migration_files


async def apply_migrations() -> None:
    """Apply SQL migrations to the local Postgres instance.

    Reads NETENGINE_DB_URL (e.g. postgresql://user:pass@host:5432/db).
    Falls back to SUPABASE_DB_* variables for backward compat with cloud setups.
    """
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
