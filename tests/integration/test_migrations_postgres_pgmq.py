"""Integration tests for the shared migration service against Postgres + pgmq."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner

from netengine.cli.main import cli
from netengine.db.migrations import (
    MIGRATIONS_DIR,
    discover_migrations,
    migration_checksum,
    run_migrations,
)
from netengine.events.queues import Queue

asyncpg = pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.slow]

POSTGRES_IMAGE = "ghcr.io/pgmq/pg15-pgmq:latest"
POSTGRES_PASSWORD = "integration_test_password"


@pytest.fixture(scope="session")
def pgmq_postgres_url() -> str:
    """Start a fresh Postgres container with pgmq extension files installed."""
    if shutil.which("docker") is None:
        pytest.skip("docker is required for pgmq migration integration tests")

    container_name = f"netengine-migration-pgmq-{uuid4().hex[:12]}"
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-e",
            f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
            "-p",
            "127.0.0.1::5432",
            POSTGRES_IMAGE,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"could not start {POSTGRES_IMAGE}: {run.stderr.strip()}")

    try:
        inspect = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
                container_name,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        port = inspect.stdout.strip()
        admin_url = f"postgresql://postgres:{POSTGRES_PASSWORD}@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 60
        while True:
            try:
                conn = asyncio.run(asyncpg.connect(admin_url))
                asyncio.run(conn.close())
                break
            except Exception:
                if time.monotonic() > deadline:
                    pytest.fail("timed out waiting for pgmq Postgres container")
                time.sleep(1)
        yield admin_url
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], check=False, capture_output=True)


@pytest.fixture()
def fresh_database_url(pgmq_postgres_url: str) -> str:
    """Create an isolated database for each migration test."""
    db_name = f"netengine_migration_{uuid4().hex}"

    async def create() -> str:
        conn = await asyncpg.connect(pgmq_postgres_url)
        try:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()
        return pgmq_postgres_url.rsplit("/", 1)[0] + f"/{db_name}"

    url = asyncio.run(create())
    yield url

    async def drop() -> None:
        conn = await asyncpg.connect(pgmq_postgres_url)
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db_name
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await conn.close()

    asyncio.run(drop())


async def _fetch(conn: object, query: str, *args: object) -> list[object]:
    return list(await conn.fetch(query, *args))


@pytest.mark.asyncio
async def test_fresh_database_applies_all_migrations_and_creates_expected_records_and_queues(
    fresh_database_url: str,
) -> None:
    result = await run_migrations(fresh_database_url)

    migration_files = discover_migrations(MIGRATIONS_DIR)
    assert result.applied_count == len(migration_files)
    assert result.failed_count == 0

    conn = await asyncpg.connect(fresh_database_url)
    try:
        records = await _fetch(
            conn,
            """
            SELECT filename, checksum, success, error
            FROM schema_migrations
            ORDER BY filename
            """,
        )
        assert [record["filename"] for record in records] == [path.name for path in migration_files]
        assert all(record["success"] is True for record in records)
        assert all(record["error"] is None for record in records)
        assert [record["checksum"] for record in records] == [
            migration_checksum(path.read_text(encoding="utf-8")) for path in migration_files
        ]

        missing_queues = []
        for queue in Queue:
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"pgmq.q_{queue.value}"
            )
            if not exists:
                missing_queues.append(queue.value)
        assert missing_queues == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rerunning_migrations_skips_all_work(fresh_database_url: str) -> None:
    first = await run_migrations(fresh_database_url)
    second = await run_migrations(fresh_database_url)

    assert first.applied_count == len(discover_migrations(MIGRATIONS_DIR))
    assert second.applied_count == 0
    assert second.skipped_count == len(discover_migrations(MIGRATIONS_DIR))
    assert second.failed_count == 0


@pytest.mark.asyncio
async def test_failed_migration_is_not_successful_and_stops_later_migrations(
    fresh_database_url: str, tmp_path: Path
) -> None:
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE before_failure (id int);", encoding="utf-8")
    (tmp_path / "002_failure.sql").write_text(
        "SELECT definitely_not_a_function();", encoding="utf-8"
    )
    (tmp_path / "003_later.sql").write_text(
        "CREATE TABLE after_failure (id int);", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="002_failure.sql"):
        await run_migrations(fresh_database_url, tmp_path)

    conn = await asyncpg.connect(fresh_database_url)
    try:
        rows = await _fetch(
            conn,
            "SELECT filename, success FROM schema_migrations ORDER BY filename",
        )
        assert [(row["filename"], row["success"]) for row in rows] == [
            ("001_ok.sql", True),
            ("002_failure.sql", False),
        ]
        assert await conn.fetchval("SELECT to_regclass('public.after_failure')") is None
    finally:
        await conn.close()


def test_migrate_status_and_check_exit_codes(fresh_database_url: str) -> None:
    runner = CliRunner()
    env = {"NETENGINE_DB_URL": fresh_database_url}

    pending_status = runner.invoke(cli, ["migrate", "status"], env=env)
    assert pending_status.exit_code == 0, pending_status.output
    assert "pending" in pending_status.output

    pending_check = runner.invoke(cli, ["migrate", "check"], env=env)
    assert pending_check.exit_code == 1, pending_check.output
    assert "pending" in pending_check.output

    applied = asyncio.run(run_migrations(fresh_database_url))
    assert applied.failed_count == 0

    current_status = runner.invoke(cli, ["migrate", "status"], env=env)
    assert current_status.exit_code == 0, current_status.output
    assert "applied" in current_status.output
    assert "0 pending" in current_status.output

    current_check = runner.invoke(cli, ["migrate", "check"], env=env)
    assert current_check.exit_code == 0, current_check.output
    assert "Migrations current" in current_check.output
