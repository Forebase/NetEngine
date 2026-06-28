"""Regression tests for pgmq queue declarations in migrations."""

from pathlib import Path
import re

from netengine.events.queues import PRIMARY_QUEUES, Queue, dlq_for


REPO_ROOT = Path(__file__).resolve().parents[1]
INITIAL_MIGRATION = REPO_ROOT / "migrations" / "001_initial.sql"
PGMQ_CREATE_RE = re.compile(r"SELECT\s+pgmq\.create\('([^']+)'\);", re.IGNORECASE)


def test_initial_migration_declares_all_registered_queues() -> None:
    """Keep the initial pgmq migration aligned with the Queue registry."""
    migration_queue_names = set(PGMQ_CREATE_RE.findall(INITIAL_MIGRATION.read_text()))
    registered_queue_names = {queue.value for queue in Queue}

    assert migration_queue_names == registered_queue_names


def test_all_primary_queues_have_registered_dlqs() -> None:
    """Keep DLQ lookups explicit rather than string-convention based."""
    for queue in PRIMARY_QUEUES:
        assert dlq_for(queue) in Queue
        assert dlq_for(queue).value in {registered.value for registered in Queue}
