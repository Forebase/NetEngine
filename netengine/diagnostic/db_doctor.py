"""Database doctor checks shared by CLI preflight surfaces."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from urllib.parse import urlparse

from netengine.diagnostic.preflight import DoctorCheckResult, DoctorStatus
from netengine.events.queues import PRIMARY_QUEUES, dlq_for

DB_URL_HINT = (
    "Start the local database with `docker compose up -d db`, then set "
    "`export NETENGINE_DB_URL=postgresql://netengine:dev_password@localhost:5432/netengine`."
)
PGMQ_HINT = "Run database migrations or install pgmq before normal alpha boot."
QUEUE_HINT = "Run `netengine migrate up`; migrations may create missing pgmq queues."


def _parse_db_url(db_url: str | None) -> DoctorCheckResult:
    if not db_url:
        return DoctorCheckResult(
            "Database URL",
            DoctorStatus.FAIL,
            "NETENGINE_DB_URL/DATABASE_URL is not set",
            DB_URL_HINT,
            "database",
        )
    parsed = urlparse(db_url)
    ok = (
        parsed.scheme in {"postgres", "postgresql"}
        and bool(parsed.hostname)
        and bool(parsed.path.strip("/"))
    )
    return DoctorCheckResult(
        "Database URL",
        DoctorStatus.OK if ok else DoctorStatus.FAIL,
        "parseable PostgreSQL URL" if ok else "not a valid PostgreSQL URL",
        None if ok else "Use a postgresql:// URL with host and database name.",
        "database",
    )


def _expected_pgmq_queues() -> set[str]:
    primary = set(PRIMARY_QUEUES)
    return {queue.value for queue in primary | {dlq_for(queue) for queue in primary}}


def _queue_name(row: object) -> str | None:
    if isinstance(row, dict):
        value = row.get("queue_name")
    else:
        try:
            value = row["queue_name"]  # type: ignore[index]
        except Exception:
            try:
                value = row[0]  # type: ignore[index]
            except Exception:
                value = None
    return str(value) if value is not None else None


async def _inspect_database(db_url: str, *, timeout: float) -> list[DoctorCheckResult]:
    try:
        import asyncpg  # type: ignore[import]
    except ImportError:
        return [
            DoctorCheckResult(
                "Postgres connectivity",
                DoctorStatus.FAIL,
                "asyncpg is not installed",
                "Install project dependencies before running database checks.",
                "database",
            )
        ]

    try:
        conn = await asyncpg.connect(db_url, timeout=timeout)
    except Exception as exc:
        return [
            DoctorCheckResult(
                "Postgres connectivity",
                DoctorStatus.FAIL,
                f"unable to connect within {timeout:g}s: {exc}",
                DB_URL_HINT,
                "database",
            )
        ]

    try:
        await asyncio.wait_for(conn.fetchval("SELECT 1;"), timeout=timeout)
        checks = [
            DoctorCheckResult(
                "Postgres connectivity",
                DoctorStatus.OK,
                "connected and SELECT 1 succeeded",
                group="database",
            )
        ]

        pgmq_exists = await asyncio.wait_for(
            conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'pgmq';"),
            timeout=timeout,
        )
        if not pgmq_exists:
            checks.append(
                DoctorCheckResult(
                    "pgmq extension",
                    DoctorStatus.FAIL,
                    "pgmq extension is not installed",
                    PGMQ_HINT,
                    "database",
                )
            )
            return checks

        checks.append(
            DoctorCheckResult(
                "pgmq extension",
                DoctorStatus.OK,
                "pgmq extension is installed",
                group="database",
            )
        )

        rows: Iterable[object] = await asyncio.wait_for(
            conn.fetch("SELECT queue_name FROM pgmq.list_queues();"), timeout=timeout
        )
        existing = {name for row in rows if (name := _queue_name(row))}
        missing = sorted(_expected_pgmq_queues() - existing)
        checks.append(
            DoctorCheckResult(
                "pgmq queues",
                DoctorStatus.WARN if missing else DoctorStatus.OK,
                (
                    "missing queues: " + ", ".join(missing)
                    if missing
                    else "all expected queues exist"
                ),
                QUEUE_HINT if missing else None,
                "database",
                required=False if missing else True,
            )
        )
        return checks
    except Exception as exc:
        return [
            DoctorCheckResult(
                "Postgres connectivity",
                DoctorStatus.FAIL,
                f"database inspection failed: {exc}",
                DB_URL_HINT,
                "database",
            )
        ]
    finally:
        await conn.close()


def check_database(
    db_url: str | None, *, timeout: float = 3.0
) -> list[DoctorCheckResult]:
    """Return actionable doctor checks for Postgres, pgmq, and event queues."""
    checks = [parsed := _parse_db_url(db_url)]
    if not db_url or parsed.status != DoctorStatus.OK:
        return checks
    return checks + asyncio.run(_inspect_database(db_url, timeout=timeout))
