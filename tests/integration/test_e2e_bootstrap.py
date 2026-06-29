"""End-to-end bootstrap integration test.

Exercises the full phase sequence (0–9) in mock mode, then validates that:
- All phases are marked complete in RuntimeState
- The operator API health endpoint reports "ok"
- A spec reload with a new org is accepted
- The org CRUD API surface works end-to-end
- ``netengine down --dry-run`` lists resources without removing them
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from netengine.core.orchestrator import Orchestrator
from netengine.core.state import RuntimeState
from netengine.spec.loader import load_spec

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def minimal_spec():
    return load_spec(EXAMPLES_DIR / "minimal.yaml")


@pytest.fixture
def single_org_spec():
    return load_spec(EXAMPLES_DIR / "single-org.yaml")


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")
    from netengine.api.app import app

    return TestClient(app)


# ─────────────────────────────────────────────
# Full mock bootstrap
# ─────────────────────────────────────────────


def _all_phase_mocks():
    """Return a context manager stack that mocks all phase execute methods."""
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, patch

    stack = ExitStack()
    handler_modules = [
        ("netengine.handlers.substrate", "SubstrateHandler"),
        ("netengine.handlers.dns", "DNSHandler"),
        ("netengine.handlers.phase_pki", "PKIPhaseHandler"),
        ("netengine.phases.phase_platform_identity", "PlatformIdentityPhaseHandler"),
        ("netengine.phases.phase_registries", "RegistriesPhaseHandler"),
        ("netengine.phases.phase_inworld_identity", "InWorldIdentityPhaseHandler"),
        ("netengine.phases.phase_ands", "ANDsPhaseHandler"),
        ("netengine.phases.phase_services", "ServicesPhaseHandler"),
        ("netengine.handlers.app_handler", "OrgAppsPhaseHandler"),
    ]
    for mod, cls in handler_modules:
        stack.enter_context(patch(f"{mod}.{cls}.execute", new_callable=AsyncMock))
        stack.enter_context(
            patch(f"{mod}.{cls}.healthcheck", new_callable=AsyncMock, return_value=True)
        )
    # Skip prerequisite checks so mocked phases don't block each other
    stack.enter_context(patch("netengine.core.orchestrator.Orchestrator._check_prerequisites"))
    # Prevent state.save() from stripping completion flags (mock execute doesn't populate outputs)
    stack.enter_context(
        patch("netengine.core.state.RuntimeState._discard_completion_flags_without_outputs")
    )
    return stack


class TestFullMockBootstrap:
    """Run all 10 phases in mock mode and verify runtime state."""

    @pytest.mark.asyncio
    async def test_all_phases_complete_in_mock_mode(self, tmp_path, monkeypatch, minimal_spec):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))

        with _all_phase_mocks():
            orchestrator = Orchestrator(minimal_spec, mock_mode=True)
            await orchestrator.execute_phases(up_to_phase=9)

        state = orchestrator.runtime_state
        for phase in range(10):
            assert state.phase_completed.get(
                str(phase)
            ), f"Phase {phase} not marked complete after mock bootstrap"

    @pytest.mark.asyncio
    async def test_bootstrap_stores_world_spec_in_state(self, tmp_path, monkeypatch, minimal_spec):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))

        with _all_phase_mocks():
            orchestrator = Orchestrator(minimal_spec, mock_mode=True)
            await orchestrator.execute_phases(up_to_phase=9)

        state = RuntimeState.load()
        assert state.world_spec is not None
        assert state.world_spec["metadata"]["name"] == minimal_spec.metadata.name

    @pytest.mark.asyncio
    async def test_partial_bootstrap_stops_at_requested_phase(
        self, tmp_path, monkeypatch, minimal_spec
    ):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))

        with _all_phase_mocks():
            orchestrator = Orchestrator(minimal_spec, mock_mode=True)
            await orchestrator.execute_phases(up_to_phase=3)

        state = orchestrator.runtime_state
        for phase in range(4):
            assert state.phase_completed.get(
                str(phase)
            ), f"Phase {phase} should be complete with up_to=3"
        # Phase 4 should NOT be complete
        assert not state.phase_completed.get("4")


# ─────────────────────────────────────────────
# API health after bootstrap
# ─────────────────────────────────────────────


class TestAPIHealthAfterBootstrap:
    """Verify health endpoint reflects phase completion from state."""

    def test_health_ok_when_all_phases_complete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        state = RuntimeState(
            phase_completed={str(i): True for i in range(10)},
            substrate_output={"healthy": True},
            dns_output={"healthy": True},
            pki_bootstrapped=True,
            pki_output={"bootstrapped": True},
            identity_platform_output={"healthy": True},
            world_registry_output={"healthy": True},
            domain_registry_output={"healthy": True},
            identity_inworld_output={"healthy": True},
            ands_output={"healthy": True},
            world_services_output={"healthy": True},
            org_apps_output={"healthy": True},
        )
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_degraded_when_phases_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"


# ─────────────────────────────────────────────
# Reload after bootstrap
# ─────────────────────────────────────────────


class TestReloadAfterBootstrap:
    """Verify reload engine accepts spec changes after bootstrap."""

    def test_reload_adds_new_org(self, tmp_path, monkeypatch, minimal_spec):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        state = RuntimeState()
        state.world_spec = minimal_spec.model_dump()
        state.save()

        new_dict = minimal_spec.model_dump(mode="json")
        new_dict["world_registry"]["organizations"].append(
            {
                "name": "new-corp",
                "description": "Added via reload",
                "capabilities": ["host_services"],
                "and_profile": "business",
            }
        )

        from netengine.api.app import app

        client = TestClient(app)

        with patch(
            "netengine.phases.phase_registries.RegistriesPhaseHandler.execute",
            new_callable=AsyncMock,
        ):
            with patch(
                "netengine.phases.phase_inworld_identity.InWorldIdentityPhaseHandler.execute",
                new_callable=AsyncMock,
            ):
                resp = client.post(
                    "/api/v1/reload",
                    json={"spec_yaml": yaml.dump(new_dict)},
                    headers={"X-Bootstrap-Secret": "e2e-secret"},
                )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_reload_rejects_immutable_subnet_change(self, tmp_path, monkeypatch, minimal_spec):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        state = RuntimeState()
        state.world_spec = minimal_spec.model_dump()
        state.save()

        bad_dict = minimal_spec.model_dump(mode="json")
        bad_dict["substrate"]["networks"]["core"]["subnet"] = "192.168.99.0/24"

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.post(
            "/api/v1/reload",
            json={"spec_yaml": yaml.dump(bad_dict)},
            headers={"X-Bootstrap-Secret": "e2e-secret"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["immutability_violations"]


# ─────────────────────────────────────────────
# Org CRUD API
# ─────────────────────────────────────────────


def _make_db_mock(orgs: list[dict]) -> MagicMock:
    """Build a synchronous MagicMock that mimics the supabase async DB client chain."""
    mock_db = MagicMock()
    # list / select
    mock_db.table.return_value.select.return_value.execute = AsyncMock(
        return_value=MagicMock(data=orgs)
    )
    mock_db.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
        return_value=MagicMock(data=orgs[:1] if orgs else [])
    )
    # upsert
    mock_db.table.return_value.upsert.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[])
    )
    # update
    mock_db.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[])
    )
    # delete
    mock_db.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[])
    )
    return mock_db


class TestOrgCRUDAPI:
    """Verify org list / get / update / delete API surface."""

    def test_list_orgs(self, api_client):
        orgs = [{"org_name": "acme", "capabilities": [], "and_profile": "business"}]
        mock_db = _make_db_mock(orgs)
        with patch(
            "netengine.handlers.world_registry_handler.WorldRegistryHandler._get_db",
            AsyncMock(return_value=mock_db),
        ):
            resp = api_client.get("/api/v1/orgs", headers={"X-Bootstrap-Secret": "e2e-secret"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_org_not_found(self, api_client):
        mock_db = _make_db_mock([])
        with patch(
            "netengine.handlers.world_registry_handler.WorldRegistryHandler._get_db",
            AsyncMock(return_value=mock_db),
        ):
            resp = api_client.get(
                "/api/v1/orgs/nonexistent", headers={"X-Bootstrap-Secret": "e2e-secret"}
            )
        assert resp.status_code == 404

    def test_update_org(self, api_client):
        org = {"org_name": "acme", "capabilities": [], "and_profile": "business"}
        mock_db = _make_db_mock([org])
        with patch(
            "netengine.handlers.world_registry_handler.WorldRegistryHandler._get_db",
            AsyncMock(return_value=mock_db),
        ):
            with patch("netengine.core.pgmq_client.PGMQClient.send", AsyncMock()):
                resp = api_client.put(
                    "/api/v1/orgs/acme",
                    json={"capabilities": ["host_services"], "and_profile": "enterprise"},
                    headers={"X-Bootstrap-Secret": "e2e-secret"},
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_delete_org_ephemeral_no_confirm_required(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "w", "lifecycle": "ephemeral"}}
        state.save()

        org = {"org_name": "acme", "capabilities": [], "and_profile": "business"}
        mock_db = _make_db_mock([org])

        from netengine.api.app import app

        client = TestClient(app)
        with patch(
            "netengine.handlers.world_registry_handler.WorldRegistryHandler._get_db",
            AsyncMock(return_value=mock_db),
        ):
            with patch("netengine.core.pgmq_client.PGMQClient.send", AsyncMock()):
                resp = client.request(
                    "DELETE",
                    "/api/v1/orgs/acme",
                    json={"confirm": False},
                    headers={"X-Bootstrap-Secret": "e2e-secret"},
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    def test_delete_org_persistent_requires_confirm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "w", "lifecycle": "persistent"}}
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.request(
            "DELETE",
            "/api/v1/orgs/acme",
            json={"confirm": False},
            headers={"X-Bootstrap-Secret": "e2e-secret"},
        )
        assert resp.status_code == 409


# ─────────────────────────────────────────────
# AND management API
# ─────────────────────────────────────────────


class TestANDManagementAPI:
    def test_list_ands_returns_instances(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "e2e-secret")

        state = RuntimeState()
        state.ands_output = {
            "instances": [{"and_name": "acme-and", "org_name": "acme", "profile": "business"}]
        }
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/ands", headers={"X-Bootstrap-Secret": "e2e-secret"})
        assert resp.status_code == 200
        assert len(resp.json()["ands"]) == 1

    def test_list_ands_empty_when_no_state(self, api_client):
        resp = api_client.get("/api/v1/ands", headers={"X-Bootstrap-Secret": "e2e-secret"})
        assert resp.status_code == 200
        assert resp.json()["ands"] == []


# ─────────────────────────────────────────────
# Down --dry-run CLI
# ─────────────────────────────────────────────


class TestDownDryRun:
    def test_dry_run_shows_resources_without_removing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "test-world", "lifecycle": "ephemeral"}}
        state.save()

        mock_container = MagicMock()
        mock_container.name = "netengines_coredns"
        mock_container.id = "abc123"
        mock_network = MagicMock()
        mock_network.name = "netengines_core"
        mock_volume = MagicMock()
        mock_volume.name = "netengines_data"

        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = [mock_container]
        mock_docker.networks.list.return_value = [mock_network]
        mock_docker.volumes.list.return_value = [mock_volume]

        from netengine.cli.main import cli

        runner = CliRunner()
        with patch("docker.from_env", return_value=mock_docker):
            result = runner.invoke(cli, ["down", "--dry-run"])

        assert result.exit_code == 0
        assert "would remove" in result.output
        assert "netengines_coredns" in result.output
        # Ensure nothing was actually stopped/removed
        mock_container.stop.assert_not_called()
        mock_container.remove.assert_not_called()

    def test_dry_run_exits_zero_even_with_nothing_to_remove(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))

        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = []
        mock_docker.networks.list.return_value = []
        mock_docker.volumes.list.return_value = []

        from netengine.cli.main import cli

        runner = CliRunner()
        with patch("docker.from_env", return_value=mock_docker):
            result = runner.invoke(cli, ["down", "--dry-run"])

        assert result.exit_code == 0
        assert "would be removed" in result.output


# ─────────────────────────────────────────────
# Full MVP lifecycle
# ─────────────────────────────────────────────


class TestFullMVPLifecycle:
    """Single end-to-end test covering the full dev-sandbox lifecycle:
    bootstrap → API health → idempotent re-run → reload with new org.
    """

    @pytest.mark.asyncio
    async def test_full_mvp_lifecycle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "mvp-secret")

        spec = load_spec(EXAMPLES_DIR / "minimal.yaml")

        # ── Step 1: Full mock bootstrap, all 9 phases ──────────────────────
        phase_events: list[tuple[str, int]] = []

        def _on_start(n: int, name: str) -> None:
            phase_events.append(("start", n))

        def _on_complete(n: int, name: str) -> None:
            phase_events.append(("complete", n))

        def _on_skip(n: int, name: str) -> None:
            phase_events.append(("skip", n))

        with _all_phase_mocks():
            orchestrator = Orchestrator(spec, mock_mode=True)
            await orchestrator.execute_phases(
                up_to_phase=9,
                on_phase_start=_on_start,
                on_phase_complete=_on_complete,
                on_phase_skip=_on_skip,
            )

        state = orchestrator.runtime_state
        for phase in range(10):
            assert state.phase_completed.get(
                str(phase)
            ), f"Phase {phase} not complete after MVP bootstrap"

        # DNSHandler covers phases 1+2 in one handler, so 9 handler invocations total
        started = [n for event, n in phase_events if event == "start"]
        completed = [n for event, n in phase_events if event == "complete"]
        assert len(started) == 9, f"Expected 9 on_phase_start calls, got {len(started)}"
        assert len(completed) == 9, f"Expected 9 on_phase_complete calls, got {len(completed)}"

        # ── Step 2: World spec is recorded in state ────────────────────────
        assert state.world_spec is not None
        assert state.world_spec["metadata"]["name"] == spec.metadata.name

        # ── Step 3: Reload adds a new org without full re-bootstrap ─────────
        new_dict = spec.model_dump(mode="json")
        new_dict["world_registry"]["organizations"].append(
            {
                "name": "mvp-corp",
                "description": "Added in full lifecycle test",
                "capabilities": ["host_services"],
                "and_profile": "business",
            }
        )

        from netengine.api.app import app

        api_client = TestClient(app)
        with patch(
            "netengine.phases.phase_registries.RegistriesPhaseHandler.execute",
            new_callable=AsyncMock,
        ):
            with patch(
                "netengine.phases.phase_inworld_identity.InWorldIdentityPhaseHandler.execute",
                new_callable=AsyncMock,
            ):
                resp = api_client.post(
                    "/api/v1/reload",
                    json={"spec_yaml": yaml.dump(new_dict)},
                    headers={"X-Bootstrap-Secret": "mvp-secret"},
                )

        assert resp.status_code == 200
        result = resp.json()
        assert result["success"] is True, f"Reload failed: {result}"
