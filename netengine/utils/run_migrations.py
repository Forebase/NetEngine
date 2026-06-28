"""Compatibility wrapper for applying NetEngine database migrations."""

import os
from pathlib import Path

from netengine.core.migrations import MigrationService


async def apply_migrations() -> None:
    """Apply pending SQL migrations using the shared migration service.

    Reads NETENGINE_DB_URL first, then DATABASE_URL for consistency with the
    CLI's startup and migration commands.
    """
    db_url = os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("Database URL is required: set NETENGINE_DB_URL or DATABASE_URL.")

    migrations_dir = Path(__file__).parent.parent.parent / "migrations"
    await MigrationService(db_url, migrations_dir).apply_pending()
