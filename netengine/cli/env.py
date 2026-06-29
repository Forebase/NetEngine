"""Shared environment helpers for NetEngine CLI commands."""

from __future__ import annotations

import os


def db_url_from_env() -> str | None:
    """Return the database URL used by CLI database operations."""
    return os.environ.get("NETENGINE_DB_URL") or os.environ.get("DATABASE_URL")
