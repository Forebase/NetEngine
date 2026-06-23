import asyncio
import os
import subprocess
from pathlib import Path


async def apply_migrations() -> None:
    """Apply SQL migrations using psql command-line tool.

    Requires environment variables:
    - SUPABASE_DB_HOST: PostgreSQL host
    - SUPABASE_DB_PORT: PostgreSQL port (default 5432)
    - SUPABASE_DB_USER: PostgreSQL user (default postgres)
    - SUPABASE_DB_PASSWORD: PostgreSQL password
    - SUPABASE_DB_NAME: Database name (default postgres)
    """
    # Get connection parameters from environment
    db_host = os.environ.get("SUPABASE_DB_HOST", "localhost")
    db_port = os.environ.get("SUPABASE_DB_PORT", "5432")
    db_user = os.environ.get("SUPABASE_DB_USER", "postgres")
    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    db_name = os.environ.get("SUPABASE_DB_NAME", "postgres")

    sql_path = Path(__file__).parent.parent / "migrations" / "001_initial.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"Migration file not found: {sql_path}")

    # Read SQL file
    sql = sql_path.read_text()

    # Run psql via subprocess
    env = os.environ.copy()
    if db_password:
        env["PGPASSWORD"] = db_password

    try:
        # Run psql in async mode using subprocess
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
