"""
Database client factory.

Returns an AsyncDBClient for the active backend:
- Default: local asyncpg pool → NETENGINE_DB_URL (Postgres in docker-compose).
- Cloud override: set SUPABASE_URL + SUPABASE_SERVICE_KEY to use Supabase cloud.

Usage:
    db = await get_db()
    result = await db.table("world_registry").upsert({...}).execute()
"""

import os
from typing import Any

from netengine.core.db_client import AsyncDBClient, get_local_db

# Imported lazily to avoid hard dependency when running local-only.
_cloud_client = None


def _use_cloud() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _get_cloud_client():
    global _cloud_client
    if _cloud_client is None:
        from supabase import create_client

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _cloud_client = create_client(url, key)
    return _cloud_client


async def get_db() -> Any:
    """Return the active database client (local asyncpg or Supabase cloud)."""
    if _use_cloud():
        return _get_cloud_client()
    return await get_local_db()


# Backward-compat alias used by older call sites.
# New code should call get_db() directly.
get_supabase = get_db
