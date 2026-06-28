"""Compatibility wrapper for applying NetEngine database migrations."""

from __future__ import annotations

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
