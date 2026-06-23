import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse


async def apply_migrations():
    """Apply SQL migrations to the local Postgres instance.

    Reads NETENGINE_DB_URL (e.g. postgresql://user:pass@host:5432/db).
    Falls back to SUPABASE_DB_* variables for backward compat with cloud setups.
    """
    db_url = os.environ.get("NETENGINE_DB_URL")

    if db_url:
        parsed = urlparse(db_url)
        db_host = parsed.hostname or "localhost"
        db_port = str(parsed.port or 5432)
        db_user = parsed.username or "netengine"
        db_password = parsed.password or ""
        db_name = (parsed.path or "/netengine").lstrip("/")
    else:
        # Backward compat: Supabase cloud connection details
        db_host = os.environ.get("SUPABASE_DB_HOST", "localhost")
        db_port = os.environ.get("SUPABASE_DB_PORT", "5432")
        db_user = os.environ.get("SUPABASE_DB_USER", "postgres")
        db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
        db_name = os.environ.get("SUPABASE_DB_NAME", "postgres")

    sql_path = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"Migration file not found: {sql_path}")

    env = os.environ.copy()
    if db_password:
        env["PGPASSWORD"] = db_password

    try:
        process = await asyncio.create_subprocess_exec(
            "psql",
            "-h",
            db_host,
            "-p",
            db_port,
            "-U",
            db_user,
            "-d",
            db_name,
            "-f",
            str(sql_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"Migration failed: {error_msg}")

    except FileNotFoundError:
        raise RuntimeError("psql command not found. Install PostgreSQL client tools.")
