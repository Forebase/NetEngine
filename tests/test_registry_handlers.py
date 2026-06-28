"""Unit tests for WorldRegistryHandler and DomainRegistryHandler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from netengine.errors import RegistryError
from netengine.events.queues import Queue

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_supabase_mock() -> MagicMock:
    """Return a mock that supports the builder chain .table().x().execute()."""
    result = MagicMock()
    result.data = []

    query = MagicMock()
    query.execute = AsyncMock(return_value=result)
    query.select = MagicMock(return_value=query)
    query.eq = MagicMock(return_value=query)
    query.upsert = MagicMock(return_value=query)

    sb = MagicMock()
    sb.table = MagicMock(return_value=query)
    sb._query = query  # expose for assertion helpers
    sb._result = result
    return sb


def _make_pgmq_mock() -> MagicMock:
    pgmq = MagicMock()
    pgmq.send = AsyncMock()
    return pgmq


# ─────────────────────────────────────────────────────────────────────────────
# WorldRegistryHandler
# ─────────────────────────────────────────────────────────────────────────────


class TestWorldRegistryHandler:
    @pytest.fixture
    def sb(self) -> MagicMock:
        return _make_supabase_mock()

    @pytest.fixture
    def handler(self, sb: MagicMock) -> "WorldRegistryHandler":  # noqa: F821
        from netengine.handlers.world_registry_handler import WorldRegistryHandler

        pgmq = _make_pgmq_mock()
        h = WorldRegistryHandler()
        h._db = sb  # pre-seed cached connection so _get_db() never hits the network
        h.pgmq = pgmq  # replace real PGMQClient with mock (handler attribute is .pgmq)
        return h

    async def test_admit_org_upserts_to_world_registry(
        self, handler: MagicMock, sb: MagicMock
    ) -> None:
        await handler.admit_org("acme", ["dns", "mail"], "residential")
        sb.table.assert_called_with("world_registry")
        sb._query.upsert.assert_called_once_with(
            {"org_name": "acme", "capabilities": ["dns", "mail"], "and_profile": "residential"}
        )

    async def test_admit_org_sends_two_pgmq_events(self, handler: MagicMock) -> None:
        await handler.admit_org("acme", [], "residential")
        assert handler.pgmq.send.call_count == 2
        queues = {call.args[0] for call in handler.pgmq.send.call_args_list}
        assert Queue.OIDC_PROVISIONING in queues
        assert Queue.AND_PROVISIONING in queues

    async def test_seed_from_spec_admits_each_org(self, handler: MagicMock) -> None:
        org1 = MagicMock()
        org1.name = "alpha"
        org1.capabilities = []
        org1.and_profile = MagicMock(value="residential")

        org2 = MagicMock()
        org2.name = "beta"
        org2.capabilities = []
        org2.and_profile = MagicMock(value="business")

        spec = MagicMock()
        spec.world_registry.organizations = [org1, org2]

        await handler.seed_from_spec(spec)
        assert handler.pgmq.send.call_count == 4  # 2 events per org

    async def test_seed_from_spec_empty_orgs(self, handler: MagicMock) -> None:
        spec = MagicMock()
        spec.world_registry.organizations = []
        await handler.seed_from_spec(spec)
        handler.pgmq.send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# DomainRegistryHandler
# ─────────────────────────────────────────────────────────────────────────────


class TestDomainRegistryHandler:
    @pytest.fixture
    def sb(self) -> MagicMock:
        return _make_supabase_mock()

    @pytest.fixture
    def handler(self, sb: MagicMock) -> "DomainRegistryHandler":  # noqa: F821
        from netengine.handlers.domain_registry_handler import DomainRegistryHandler

        pgmq = _make_pgmq_mock()
        h = DomainRegistryHandler()
        h._db = sb  # pre-seed cached connection so _get_db() never hits the network
        h.pgmq = pgmq  # replace real PGMQClient with mock
        return h

    async def test_allocate_address_raises_when_no_pool(
        self, handler: MagicMock, sb: MagicMock
    ) -> None:
        sb._result.data = []
        with pytest.raises(RegistryError, match="No address pool"):
            await handler.allocate_address("office1", "residential")

    async def test_allocate_address_returns_cidr(self, handler: MagicMock, sb: MagicMock) -> None:
        sb._result.data = [{"cidr": "10.1.0.0/24"}]
        cidr = await handler.allocate_address("office1", "residential")
        assert cidr == "10.1.0.0/24"

    async def test_allocate_address_upserts_lease(self, handler: MagicMock, sb: MagicMock) -> None:
        sb._result.data = [{"cidr": "10.1.0.0/24"}]
        await handler.allocate_address("office1", "residential")
        upsert_calls = sb._query.upsert.call_args_list
        lease_call = next(
            (c for c in upsert_calls if c.args and "and_name" in (c.args[0] or {})), None
        )
        assert lease_call is not None
        assert lease_call.args[0]["and_name"] == "office1"

    async def test_register_domain_upserts_record(self, handler: MagicMock, sb: MagicMock) -> None:
        await handler.register_domain("acme.internal", "acme", ["ns1.internal"])
        sb.table.assert_called_with("domain_records")
        sb._query.upsert.assert_called_with(
            {"domain": "acme.internal", "org_name": "acme", "ns_records": ["ns1.internal"]}
        )

    async def test_register_domain_sends_dns_update_event(self, handler: MagicMock) -> None:
        await handler.register_domain("acme.internal", "acme", [])
        handler.pgmq.send.assert_called_once()
        assert handler.pgmq.send.call_args.args[0] == Queue.DNS_UPDATES

    async def test_seed_address_pools_upserts_each_pool(
        self, handler: MagicMock, sb: MagicMock
    ) -> None:
        pool1 = MagicMock()
        pool1.label = "residential"
        pool1.cidr = "10.1.0.0/16"

        pool2 = MagicMock()
        pool2.label = "business"
        pool2.cidr = "10.2.0.0/16"

        spec = MagicMock()
        spec.domain_registry.address_space = [pool1, pool2]

        await handler.seed_address_pools(spec)
        assert sb._query.upsert.call_count == 2
