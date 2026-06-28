import asyncio
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
