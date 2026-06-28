"""Regression tests for event queue DLQ mappings."""

from netengine.events.queues import DLQ_BY_PRIMARY, PRIMARY_QUEUES, Queue, dlq_for


def test_every_primary_queue_has_exactly_one_dlq_mapping() -> None:
    """Keep the explicit DLQ map aligned with the primary queue registry."""
    assert set(DLQ_BY_PRIMARY) == set(PRIMARY_QUEUES)
    assert len(DLQ_BY_PRIMARY) == len(PRIMARY_QUEUES)
    assert len(set(DLQ_BY_PRIMARY.values())) == len(PRIMARY_QUEUES)


def test_every_mapped_dlq_exists_in_queue_registry() -> None:
    """Each mapped DLQ must be a Queue enum member and not another primary queue."""
    queue_members = set(Queue)
    primary_queues = set(PRIMARY_QUEUES)

    for primary, dlq in DLQ_BY_PRIMARY.items():
        assert primary in primary_queues
        assert dlq in queue_members
        assert dlq not in primary_queues
        assert dlq_for(primary) is dlq
