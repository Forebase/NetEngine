"""Shared PostgreSQL migration service with explicit partial-failure semantics."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

MIGRATION_TABLE = "netengine_schema_migrations"


class MigrationState(str, Enum):
    """Operator-facing migration status values."""

    APPLIED = "applied"
    PENDING = "pending"
    FAILED = "failed"
    CHECKSUM_DRIFT = "checksum-drift"


@dataclass(frozen=True)
class MigrationStatus:
    filename: str
    checksum: str
    state: MigrationState
    applied_at: str | None = None
    error: str | None = None


class MigrationApplyError(RuntimeError):
    """Raised when a migration fails and later migrations must not run."""

    def __init__(self, filename: str, statement_context: str | None, database_error: BaseException):
        self.filename = filename
        self.statement_context = statement_context
        self.database_error = database_error
        context = f" Statement context: {statement_context}" if statement_context else ""
        super().__init__(f"Migration {filename} failed.{context} Database error: {database_error}")


NON_TRANSACTIONAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\bCREATE\s+DATABASE\b",
        r"\bDROP\s+DATABASE\b",
        r"\bCREATE\s+INDEX\s+CONCURRENTLY\b",
        r"\bREINDEX\b.*\bCONCURRENTLY\b",
        r"\bVACUUM\b",
        r"\bALTER\s+SYSTEM\b",
        r"\bCREATE\s+TABLESPACE\b",
        r"\bDROP\s+TABLESPACE\b",
    )
)


def migration_checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def postgres_allows_transaction(sql: str) -> bool:
    """Return False for common PostgreSQL operations forbidden in transaction blocks."""
    return not any(pattern.search(sql) for pattern in NON_TRANSACTIONAL_PATTERNS)


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL on semicolons while preserving quoted and dollar-quoted bodies."""
    statements: list[str] = []
    start = 0
    i = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment = False

    while i < len(sql):
        ch = sql[i]
        nxt = sql[i : i + 2]

        if line_comment:
            if ch == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if nxt == "*/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if quote:
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                else:
                    quote = None
                    i += 1
            else:
                i += 1
            continue
        if dollar_tag:
            if sql.startswith(dollar_tag, i):
                i += len(dollar_tag)
                dollar_tag = None
            else:
                i += 1
            continue

        if nxt == "--":
            line_comment = True
            i += 2
            continue
        if nxt == "/*":
            block_comment = True
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if match:
                dollar_tag = match.group(0)
                i += len(dollar_tag)
                continue
        if ch == ";":
            statement = sql[start : i + 1].strip()
            if statement:
                statements.append(statement)
            start = i + 1
        i += 1

    tail = sql[start:].strip()
    if tail:
        statements.append(tail)
    return statements


class MigrationService:
    def __init__(
        self, db_url: str, migrations_dir: Path, logger: Callable[[str], None] | None = None
    ):
        self.db_url = db_url
        self.migrations_dir = migrations_dir
        self.logger = logger or (lambda message: None)

    async def apply(self) -> list[MigrationStatus]:
        asyncpg = _asyncpg()
        conn = await asyncpg.connect(self.db_url)
        try:
            await self._ensure_table(conn)
            statuses = await self.status(conn)
            for status in statuses:
                self.logger(f"Migration {status.filename}: {status.state.value}")
                if status.state in {MigrationState.APPLIED, MigrationState.CHECKSUM_DRIFT}:
                    continue
                await self._apply_one(conn, self.migrations_dir / status.filename, status.checksum)
            return await self.status(conn)
        finally:
            await conn.close()

    async def status(self, conn: "asyncpg.Connection | None" = None) -> list[MigrationStatus]:
        close_conn = conn is None
        if conn is None:
            asyncpg = _asyncpg()
            conn = await asyncpg.connect(self.db_url)
        try:
            await self._ensure_table(conn)
            rows = await conn.fetch(f"SELECT * FROM {MIGRATION_TABLE}")
            by_name = {row["filename"]: row for row in rows}
            statuses: list[MigrationStatus] = []
            for path in self._migration_files():
                sql = path.read_text()
                checksum = migration_checksum(sql)
                row = by_name.get(path.name)
                if row is None:
                    state = MigrationState.PENDING
                elif not row["success"]:
                    state = MigrationState.FAILED
                elif row["checksum"] != checksum:
                    state = MigrationState.CHECKSUM_DRIFT
                else:
                    state = MigrationState.APPLIED
                statuses.append(
                    MigrationStatus(
                        filename=path.name,
                        checksum=checksum,
                        state=state,
                        applied_at=str(row["applied_at"]) if row and row["applied_at"] else None,
                        error=row["error"] if row else None,
                    )
                )
            return statuses
        finally:
            if close_conn:
                await conn.close()

    async def _apply_one(self, conn: "asyncpg.Connection", path: Path, checksum: str) -> None:
        sql = path.read_text()
        statements = split_sql_statements(sql)
        transactional = postgres_allows_transaction(sql)
        context: str | None = None
        try:
            if transactional:
                async with conn.transaction():
                    for statement in statements:
                        context = _statement_context(statement)
                        await conn.execute(statement)
            else:
                self.logger(
                    f"Migration {path.name}: pending (non-transactional operations detected)"
                )
                for statement in statements:
                    context = _statement_context(statement)
                    await conn.execute(statement)
            await conn.execute(
                f"""
                INSERT INTO {MIGRATION_TABLE} (filename, checksum, success, error)
                VALUES ($1, $2, TRUE, NULL)
                ON CONFLICT (filename) DO UPDATE
                SET checksum = EXCLUDED.checksum, success = TRUE, error = NULL, applied_at = now()
                """,
                path.name,
                checksum,
            )
            self.logger(f"Migration {path.name}: applied")
        except Exception as exc:
            await conn.execute(
                f"""
                INSERT INTO {MIGRATION_TABLE} (filename, checksum, success, error)
                VALUES ($1, $2, FALSE, $3)
                ON CONFLICT (filename) DO UPDATE
                SET checksum = EXCLUDED.checksum, success = FALSE, error = EXCLUDED.error, applied_at = now()
                """,
                path.name,
                checksum,
                f"{context or 'unknown statement'}: {exc}",
            )
            self.logger(f"Migration {path.name}: failed")
            raise MigrationApplyError(path.name, context, exc) from exc

    async def _ensure_table(self, conn: "asyncpg.Connection") -> None:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                filename TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                error TEXT,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)

    def _migration_files(self) -> Iterable[Path]:
        return sorted(self.migrations_dir.glob("*.sql"))


def _statement_context(statement: str, max_length: int = 240) -> str:
    normalized = " ".join(statement.split())
    return normalized[:max_length]


def _asyncpg() -> Any:
    try:
        import asyncpg
    except ModuleNotFoundError as exc:
        raise RuntimeError("asyncpg is required to run database migrations") from exc
    return asyncpg
