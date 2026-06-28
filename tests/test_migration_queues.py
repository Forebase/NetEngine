"""Regression tests for pgmq queue declarations in migrations."""

from pathlib import Path
import re

from netengine.events.queues import Queue


REPO_ROOT = Path(__file__).resolve().parents[1]
INITIAL_MIGRATION = REPO_ROOT / "migrations" / "001_initial.sql"
PGMQ_QUEUE_ARRAY_RE = re.compile(
    r"FOREACH\s+queue_to_create\s+IN\s+ARRAY\s+ARRAY\[(.*?)\]\s+LOOP",
    re.IGNORECASE | re.DOTALL,
)
SQL_STRING_RE = re.compile(r"'([^']+)'")


def test_initial_migration_declares_all_registered_queues() -> None:
    """Keep the initial pgmq migration aligned with the Queue registry."""
    migration = INITIAL_MIGRATION.read_text()
    match = PGMQ_QUEUE_ARRAY_RE.search(migration)
    assert match is not None
    migration_queue_names = set(SQL_STRING_RE.findall(match.group(1)))
    registered_queue_names = {queue.value for queue in Queue}

    assert migration_queue_names == registered_queue_names
