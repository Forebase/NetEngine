"""Tests for migration ledger bookkeeping."""

from pathlib import Path
from types import TracebackType

import pytest

from netengine.utils.migrations import (
    MIGRATION_LEDGER_SQL,
    apply_migration_files,
    migration_checksum,
)


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


class FakeConnection:
    def __init__(self, checksums: dict[str, str] | None = None) -> None:
        self.checksums = checksums or {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        if sql.strip().startswith("INSERT INTO netengine_migration_ledger"):
            filename, checksum = args[:2]
            self.checksums[str(filename)] = str(checksum)
        return "OK"

    async def fetchval(self, sql: str, *args: object) -> str | None:
        return self.checksums.get(str(args[0]))

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


@pytest.mark.asyncio
async def test_apply_migration_files_records_success_after_running_sql(tmp_path: Path) -> None:
    migration = tmp_path / "001_initial.sql"
    migration.write_text("CREATE TABLE example (id int primary key);")
    conn = FakeConnection()

    applied_count = await apply_migration_files(conn, [migration])

    assert applied_count == 1
    assert conn.executed[0] == (MIGRATION_LEDGER_SQL, ())
    assert conn.executed[1] == (migration.read_text(), ())
    assert conn.executed[2][1][:2] == (migration.name, migration_checksum(migration.read_text()))
    assert len(conn.executed[2][1]) == 3


@pytest.mark.asyncio
async def test_apply_migration_files_skips_matching_ledger_entry(tmp_path: Path) -> None:
    migration = tmp_path / "001_initial.sql"
    migration.write_text("SELECT 1;")
    conn = FakeConnection({migration.name: migration_checksum(migration.read_text())})

    applied_count = await apply_migration_files(conn, [migration])

    assert applied_count == 0
    assert [entry[0] for entry in conn.executed] == [MIGRATION_LEDGER_SQL]


@pytest.mark.asyncio
async def test_apply_migration_files_aborts_when_applied_migration_changed(tmp_path: Path) -> None:
    migration = tmp_path / "001_initial.sql"
    migration.write_text("SELECT 2;")
    conn = FakeConnection({migration.name: migration_checksum("SELECT 1;")})

    with pytest.raises(RuntimeError, match="Applied migration changed: 001_initial.sql"):
        await apply_migration_files(conn, [migration])

    assert [entry[0] for entry in conn.executed] == [MIGRATION_LEDGER_SQL]
