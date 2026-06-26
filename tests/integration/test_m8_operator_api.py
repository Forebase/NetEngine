"""M8 Operator API integration tests.

Tests the full FastAPI route surface, reload engine, and CLI commands.
All Docker / Supabase / Keycloak calls are mocked — tests run without live services.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from netengine.core.reload import IMMUTABLE_PATHS, DiffEntry, check_immutability, compute_diff
from netengine.core.state import RuntimeState
from netengine.spec.loader import load_spec
from netengine.spec.models import NetEngineSpec

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def _load_example(name: str) -> NetEngineSpec:
    return load_spec(EXAMPLES_DIR / name)


def _make_client(monkeypatch, tmp_path) -> TestClient:
    """Return a pre-auth TestClient using monkeypatched env."""
    monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")
    monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
    from netengine.api.app import app

    return TestClient(app)


# ─────────────────────────────────────────────
# Reload engine unit tests
# ─────────────────────────────────────────────


class TestImmutabilityCheck:
    def test_identical_specs_produce_no_violations(self):
        spec = _load_example("minimal.yaml")
        violations = check_immutability(spec, spec)
        assert violations == []

    def test_changing_network_cidr_is_a_violation(self):
        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        old_dict["substrate"]["networks"]["core"]["subnet"] = "172.99.0.0/16"
        new = NetEngineSpec(**old_dict)
        violations = check_immutability(old, new)
        assert any("substrate.networks" in v for v in violations)

    def test_changing_dns_root_ip_is_a_violation(self):
        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        old_dict["dns"]["root"]["listen_ip"] = "10.1.2.3"
        new = NetEngineSpec(**old_dict)
        violations = check_immutability(old, new)
        assert any("dns.root.listen_ip" in v for v in violations)

    def test_changing_pki_acme_ip_is_a_violation(self):
        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        old_dict["pki"]["acme"]["listen_ip"] = "10.1.2.4"
        new = NetEngineSpec(**old_dict)
        violations = check_immutability(old, new)
        assert any("pki.acme.listen_ip" in v for v in violations)

    def test_changing_lifecycle_is_a_violation(self):
        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        old_dict["metadata"]["lifecycle"] = "persistent"
        new = NetEngineSpec(**old_dict)
        violations = check_immutability(old, new)
        assert any("metadata.lifecycle" in v for v in violations)

    def test_mutable_changes_produce_no_violations(self):
        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        # Org description change — mutable
        if old_dict["world_registry"]["organizations"]:
            old_dict["world_registry"]["organizations"][0]["description"] = "updated"
        new = NetEngineSpec(**old_dict)
        violations = check_immutability(old, new)
        assert violations == []


class TestComputeDiff:
    def test_identical_specs_have_empty_diff(self):
        spec = _load_example("minimal.yaml")
        diff = compute_diff(spec, spec)
        assert diff == []

    def test_adding_org_appears_in_diff(self):
        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        new_org = {
            "name": "new-org",
            "description": "Test org",
            "capabilities": ["host_services"],
            "and_profile": "business",
        }
        old_dict["world_registry"]["organizations"].append(new_org)
        new = NetEngineSpec(**old_dict)
        diff = compute_diff(old, new)
        sections = [e.section for e in diff]
        assert "world_registry" in sections

    def test_diff_entries_have_required_fields(self):
        old = _load_example("single-org.yaml")
        old_dict = old.model_dump()
        old_dict["world_registry"]["organizations"][0]["description"] = "changed"
        new = NetEngineSpec(**old_dict)
        diff = compute_diff(old, new)
        for entry in diff:
            assert isinstance(entry, DiffEntry)
            assert entry.section
            assert entry.change_type in ("added", "removed", "updated")
            assert entry.detail


class TestApplyReload:
    @pytest.mark.asyncio
    async def test_reload_rejects_on_immutable_violation(self):
        from netengine.core.reload import apply_reload

        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        old_dict["dns"]["root"]["listen_ip"] = "10.1.2.3"
        new = NetEngineSpec(**old_dict)
        state = RuntimeState()

        result = await apply_reload(old, new, state, is_ephemeral=True)
        assert not result.success
        assert result.immutability_violations

    @pytest.mark.asyncio
    async def test_reload_no_changes_returns_success(self):
        from netengine.core.reload import apply_reload

        spec = _load_example("minimal.yaml")
        state = RuntimeState()
        result = await apply_reload(spec, spec, state, is_ephemeral=True)
        assert result.success
        assert not result.applied

    @pytest.mark.asyncio
    async def test_persistent_mode_refuses_pki_reconfig(self):
        from netengine.core.reload import apply_reload

        old = _load_example("minimal.yaml")
        old_dict = old.model_dump()
        old_dict["metadata"]["lifecycle"] = "persistent"
        old = NetEngineSpec(**old_dict)

        new_dict = old_dict.copy()
        new_dict["pki"]["root_ca"]["cn"] = "Different CA"
        new = NetEngineSpec(**new_dict)

        state = RuntimeState()
        result = await apply_reload(old, new, state, is_ephemeral=False)
        assert not result.success
        assert any("PKI" in e for e in result.errors)


# ─────────────────────────────────────────────
# API route tests
# ─────────────────────────────────────────────


class TestHealthRoute:
    def test_health_returns_ok_when_no_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")
        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "phases" in data
        assert set(data["phases"].keys()) == {
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
        }
        assert data["phases"]["9"] == {"label": "Org applications", "completed": False}

    def test_health_reports_degraded_when_phases_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")
        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.json()["status"] == "degraded"

    def test_health_stays_degraded_when_phase_9_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")
        state = RuntimeState(
            phase_completed={str(phase): True for phase in range(9)},
            substrate_output={"healthy": True},
            dns_output={"healthy": True},
            pki_bootstrapped=True,
            identity_platform_output={"healthy": True},
            world_registry_output={"healthy": True},
            domain_registry_output={"healthy": True},
            identity_inworld_output={"healthy": True},
            ands_output={"healthy": True},
            world_services_output={"healthy": True},
        )
        state.save()
        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/health")
        data = resp.json()

        assert resp.status_code == 200
        assert data["status"] == "degraded"
        assert data["phases"]["9"] == {"label": "Org applications", "completed": False}


class TestWorldRoute:
    def test_get_world_requires_auth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "real-secret")
        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/world")
        assert resp.status_code == 401

    def test_get_world_with_bootstrap_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")
        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/world", headers={"X-Bootstrap-Secret": "test-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert "phase_completed" in data

    def test_get_world_returns_stored_spec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        # Seed state with a spec
        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "test-world", "lifecycle": "ephemeral"}}
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/world", headers={"X-Bootstrap-Secret": "test-secret"})
        assert resp.status_code == 200
        assert resp.json()["spec"]["metadata"]["name"] == "test-world"


class TestReloadRoute:
    def test_reload_returns_409_when_no_running_world(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")
        from netengine.api.app import app

        client = TestClient(app)

        spec = _load_example("minimal.yaml")
        resp = client.post(
            "/api/v1/reload",
            json={"spec_yaml": yaml.dump(spec.model_dump(mode="json"))},
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 409

    def test_reload_rejects_immutable_field_change(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        spec = _load_example("minimal.yaml")
        state = RuntimeState()
        state.world_spec = spec.model_dump()
        state.save()

        # Mutate an immutable field
        new_dict = spec.model_dump(mode="json")
        new_dict["dns"]["root"]["listen_ip"] = "10.99.0.1"

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.post(
            "/api/v1/reload",
            json={"spec_yaml": yaml.dump(new_dict)},
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["immutability_violations"]

    def test_reload_with_no_changes_returns_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        spec = _load_example("minimal.yaml")
        state = RuntimeState()
        state.world_spec = spec.model_dump()
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.post(
            "/api/v1/reload",
            json={"spec_yaml": yaml.dump(spec.model_dump(mode="json"))},
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestServicesRoute:
    def test_services_returns_containers_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = []

        with patch("docker.from_env", return_value=mock_docker):
            from netengine.api.app import app

            client = TestClient(app)
            resp = client.get("/api/v1/services", headers={"X-Bootstrap-Secret": "test-secret"})

        assert resp.status_code == 200
        assert "containers" in resp.json()


class TestDNSRoute:
    def test_dns_query_calls_dig(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"192.168.1.10\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            from netengine.api.app import app

            client = TestClient(app)
            resp = client.get(
                "/api/v1/dns/gitea.acme.internal",
                headers={"X-Bootstrap-Secret": "test-secret"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "gitea.acme.internal"
        assert "answers" in data


class TestTeardownRoute:
    def test_teardown_ephemeral_world(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "t", "lifecycle": "ephemeral"}}
        state.save()

        mock_docker = MagicMock()
        mock_docker.containers.list.return_value = []
        mock_docker.networks.list.return_value = []
        mock_docker.volumes.list.return_value = []

        with patch("docker.from_env", return_value=mock_docker):
            from netengine.api.app import app

            client = TestClient(app)
            resp = client.request(
                "DELETE",
                "/api/v1/world",
                json={"confirm": False},
                headers={"X-Bootstrap-Secret": "test-secret"},
            )

        assert resp.status_code == 200

    def test_teardown_persistent_requires_confirm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "t", "lifecycle": "persistent"}}
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.request(
            "DELETE",
            "/api/v1/world",
            json={"confirm": False},
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 409


class TestExportImportRoutes:
    def test_export_returns_spec_and_phase_data(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "ephemeral"}}
        state.phase_completed = {"0": True, "1": True}
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/export", headers={"X-Bootstrap-Secret": "test-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert "spec" in data
        assert "phase_completed" in data
        assert "exported_at" in data
        assert data["schema_version"] == "netengine.import.v1"

    def test_import_updates_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        # Seed a persistent world
        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        spec = _load_example("minimal.yaml").model_dump(mode="json")
        spec["metadata"]["lifecycle"] = "persistent"

        resp = client.post(
            "/api/v1/import",
            json={
                "schema_version": "netengine.import.v1",
                "spec": spec,
                "phase_completed": {"0": True, "1": True, "2": True},
                "substrate_output": {"networks": ["platform", "core"]},
                "dns_output": {"root_ip": "10.0.0.2"},
            },
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 200
        assert "0" in resp.json()["phases_restored"]

    def test_import_rejects_invalid_spec(self, tmp_path, monkeypatch):
        client = _make_client(monkeypatch, tmp_path)

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        resp = client.post(
            "/api/v1/import",
            json={
                "schema_version": "netengine.import.v1",
                "spec": {"metadata": {"name": "broken", "lifecycle": "persistent"}},
                "phase_completed": {},
            },
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 422
        assert "Spec parse error" in resp.json()["detail"]

    def test_import_rejects_unknown_phase(self, tmp_path, monkeypatch):
        client = _make_client(monkeypatch, tmp_path)

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        spec = _load_example("minimal.yaml").model_dump(mode="json")
        spec["metadata"]["lifecycle"] = "persistent"
        resp = client.post(
            "/api/v1/import",
            json={
                "schema_version": "netengine.import.v1",
                "spec": spec,
                "phase_completed": {"99": True},
            },
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 422
        assert "Unknown phase ID" in resp.json()["detail"]

    def test_import_rejects_phase_completion_without_required_output(
        self, tmp_path, monkeypatch
    ):
        client = _make_client(monkeypatch, tmp_path)

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        spec = _load_example("minimal.yaml").model_dump(mode="json")
        spec["metadata"]["lifecycle"] = "persistent"
        resp = client.post(
            "/api/v1/import",
            json={
                "schema_version": "netengine.import.v1",
                "spec": spec,
                "phase_completed": {"0": True},
            },
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 422
        assert "missing required output" in resp.json()["detail"]

    def test_import_rejects_skipped_prerequisite_phase(self, tmp_path, monkeypatch):
        client = _make_client(monkeypatch, tmp_path)

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        spec = _load_example("minimal.yaml").model_dump(mode="json")
        spec["metadata"]["lifecycle"] = "persistent"
        resp = client.post(
            "/api/v1/import",
            json={
                "schema_version": "netengine.import.v1",
                "spec": spec,
                "phase_completed": {"0": True, "2": True},
                "substrate_output": {"networks": ["platform", "core"]},
                "dns_output": {"root_ip": "10.0.0.2"},
            },
            headers={"X-Bootstrap-Secret": "test-secret"},
        )
        assert resp.status_code == 422
        assert "Impossible phase combination" in resp.json()["detail"]
    def test_export_sanitizes_secret_phase_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        state = RuntimeState()
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "ephemeral"}}
        state.ca_cert_pem = "-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----"
        state.pki_output = {
            "ca_dns": "ca.platform.internal",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
            "nested": {"client_secret": "secret", "public": "ok"},
        }
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/export", headers={"X-Bootstrap-Secret": "test-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ca_cert_pem"] == state.ca_cert_pem
        assert data["pki_output"]["ca_dns"] == "ca.platform.internal"
        assert "private_key_pem" not in data["pki_output"]
        assert "client_secret" not in data["pki_output"]["nested"]
        assert data["pki_output"]["nested"]["public"] == "ok"

    def test_lower_privilege_user_cannot_export(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        state = RuntimeState()
        state.phase_completed = {"4": True}
        state.identity_platform_output = {"ready": True}
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        from netengine.api.app import app
        from netengine.api.auth import require_auth

        async def operator_user():
            return {"sub": "operator", "realm_access": {"roles": ["operator"]}}

        app.dependency_overrides[require_auth] = operator_user
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/export", headers={"Authorization": "Bearer user-token"})
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403

    def test_lower_privilege_user_cannot_import(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        state = RuntimeState()
        state.phase_completed = {"4": True}
        state.identity_platform_output = {"ready": True}
        state.world_spec = {"metadata": {"name": "x", "lifecycle": "persistent"}}
        state.save()

        from netengine.api.app import app
        from netengine.api.auth import require_auth

        async def operator_user():
            return {"sub": "operator", "realm_access": {"roles": ["operator"]}}

        app.dependency_overrides[require_auth] = operator_user
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/import",
                json={
                    "spec": {"metadata": {"name": "restored", "lifecycle": "persistent"}},
                    "phase_completed": {"0": True},
                },
                headers={"Authorization": "Bearer user-token"},
            )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403


class TestQueuesRoute:
    def test_queues_returns_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        mock_supabase = AsyncMock()
        mock_supabase.rpc.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        with patch("netengine.core.supabase_client.get_supabase", return_value=mock_supabase):
            from netengine.api.app import app

            client = TestClient(app)
            resp = client.get("/api/v1/queues", headers={"X-Bootstrap-Secret": "test-secret"})

        assert resp.status_code == 200
        assert "queues" in resp.json()


class TestIdentityRealmsRoute:
    def test_realms_returns_platform_and_inworld(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NETENGINES_BOOTSTRAP_SECRET", "test-secret")

        state = RuntimeState()
        state.identity_platform_output = {
            "realm_name": "platform",
            "issuer": "https://auth.platform.internal/realms/platform",
            "user_count": 1,
        }
        state.identity_inworld_output = {
            "realm_name": "inworld",
            "issuer": "https://auth.internal/realms/inworld",
            "org_realms": ["acme-corp"],
        }
        state.save()

        from netengine.api.app import app

        client = TestClient(app)
        resp = client.get("/api/v1/identity/realms", headers={"X-Bootstrap-Secret": "test-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["platform_realm"]["realm"] == "platform"
        assert data["inworld_realm"]["realm"] == "inworld"
