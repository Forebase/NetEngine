from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.state import RuntimeState
from netengine.errors import RegistryError
from netengine.handlers.context import PhaseContext
from netengine.handlers.domain_registry_handler import DomainRegistryHandler
from netengine.logs import get_logger
from netengine.phases.phase_ands import ANDsPhaseHandler


class Result:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.op = None
        self.data = None
        self.filters = []

    def select(self, cols="*"):
        self.op = "select"
        return self

    def insert(self, data):
        self.op = "insert"
        self.data = data
        return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, data):
        self.op = "upsert"
        self.data = data
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    async def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.op == "select":
            out = rows
            for col, val in self.filters:
                out = [r for r in out if r.get(col) == val]
            return Result([dict(r) for r in out])
        if self.op == "insert":
            if self.name == "address_leases":
                if any(r["and_name"] == self.data["and_name"] for r in rows):
                    raise Exception("duplicate and_name")
                if any(r["cidr"] == self.data["cidr"] for r in rows):
                    raise Exception("duplicate cidr")
            rows.append(dict(self.data))
            return Result([dict(self.data)])
        if self.op == "delete":
            keep = []
            deleted = []
            for r in rows:
                match = all(r.get(c) == v for c, v in self.filters)
                (deleted if match else keep).append(r)
            self.db[self.name] = keep
            return Result(deleted)
        rows.append(dict(self.data))
        return Result([dict(self.data)])


class FakeDB(dict):
    def table(self, name):
        return FakeTable(self, name)


@pytest.mark.asyncio
async def test_registry_allocates_unique_subnets_and_reports_exhaustion():
    db = FakeDB(address_pools=[{"profile": "tiny", "cidr": "10.0.0.0/23"}], address_leases=[])
    handler = DomainRegistryHandler(pgmq=MagicMock())
    handler._db = db

    assert await handler.allocate_address("and-a", "tiny") == "10.0.0.0/24"
    assert await handler.allocate_address("and-b", "tiny") == "10.0.1.0/24"
    with pytest.raises(RegistryError, match="exhausted"):
        await handler.allocate_address("and-c", "tiny")


@pytest.mark.asyncio
async def test_registry_skips_cidr_collisions_when_existing_lease_uses_candidate():
    db = FakeDB(
        address_pools=[{"profile": "biz", "cidr": "10.10.0.0/22"}],
        address_leases=[{"and_name": "other", "cidr": "10.10.0.0/24"}],
    )
    handler = DomainRegistryHandler(pgmq=MagicMock())
    handler._db = db

    assert await handler.allocate_address("new-and", "biz") == "10.10.1.0/24"


def _context(profile):
    state = RuntimeState()
    state.substrate_output = {"networks": {}}
    state.dns_output = {"root_zone": {}, "zone_files": {"internal": "$ORIGIN internal.\n"}}
    state.domain_registry_output = {"pools": {}}
    and_inst = SimpleNamespace(
        name="lab", org="labco", profile="business", dns_suffix="lab.internal"
    )
    spec = SimpleNamespace(
        ands=SimpleNamespace(profiles={"business": profile}, instances=[and_inst])
    )
    return PhaseContext(runtime_state=state, spec=spec, logger=get_logger("test"))


@pytest.mark.asyncio
async def test_profile_features_configure_dynamic_ip_reverse_dns_and_optional_bgp():
    profile = SimpleNamespace(dynamic_ip=True, reverse_dns=True, bgp="optional")
    ctx = _context(profile)
    handler = ANDsPhaseHandler()
    docker = AsyncMock()
    gateway = AsyncMock(gateway_container="gw")
    gateway.generate_rules.return_value = "rules"
    gateway.setup_bgp.side_effect = RuntimeError("sidecar unavailable")

    with patch.object(handler, "_allocate_address", AsyncMock(return_value="172.20.1.0/24")):
        await handler._provision_and(
            ctx, docker, gateway, ctx.spec.ands.instances[0], ctx.spec.ands
        )

    gateway.setup_dhcp.assert_awaited_once()
    gateway.setup_bgp.assert_awaited_once()
    assert "1.20.172.in-addr.arpa" in ctx.runtime_state.dns_output["zone_files"]


@pytest.mark.asyncio
async def test_required_bgp_failure_aborts_provisioning():
    profile = SimpleNamespace(dynamic_ip=False, reverse_dns=False, bgp="required")
    ctx = _context(profile)
    handler = ANDsPhaseHandler()
    docker = AsyncMock()
    gateway = AsyncMock(gateway_container="gw")
    gateway.generate_rules.return_value = "rules"
    gateway.setup_bgp.side_effect = RuntimeError("sidecar unavailable")

    with patch.object(handler, "_allocate_address", AsyncMock(return_value="172.20.2.0/24")):
        with pytest.raises(RuntimeError, match="Required BGP setup failed"):
            await handler._provision_and(
                ctx, docker, gateway, ctx.spec.ands.instances[0], ctx.spec.ands
            )


@pytest.mark.asyncio
async def test_reconcile_repairs_missing_network_after_partial_failure():
    profile = SimpleNamespace(dynamic_ip=True, reverse_dns=True, bgp=None)
    ctx = _context(profile)
    ctx.runtime_state.ands_instances["lab"] = {
        "name": "lab",
        "org": "labco",
        "profile": "business",
        "cidr": "172.20.3.0/24",
        "gateway_ip": "172.20.3.1",
        "bridge_name": "netengines_and_lab",
        "dns_suffix": "lab.internal",
        "dynamic_ip": True,
        "reverse_dns": True,
        "bgp": None,
    }
    handler = ANDsPhaseHandler()
    docker = AsyncMock()
    docker.client = MagicMock()
    docker.client.networks.get.side_effect = Exception("missing")
    gateway = AsyncMock(gateway_container="gw")
    gateway.generate_rules.return_value = "rules"

    actions = await handler.reconcile(ctx, docker, gateway)

    assert actions["repaired"] == ["lab"]
    docker.create_network.assert_awaited_once()
    gateway.setup_dhcp.assert_awaited_once()
    assert "3.20.172.in-addr.arpa" in ctx.runtime_state.dns_output["zone_files"]
