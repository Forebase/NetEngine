import asyncio
import json
import stat
import types

from netengine.core.state import RuntimeState
from netengine.security.redaction import redact_for_api, redact_for_support_bundle


def test_runtime_state_uses_env_path(tmp_path, monkeypatch):
    state_path = tmp_path / "state" / "netengine_state.json"
    monkeypatch.setenv("NETENGINE_STATE_FILE", str(state_path))

    state = RuntimeState()
    state.save()

    assert state_path.exists()
    assert not (tmp_path / "netengine_state.json").exists()


def test_load_discards_completed_phase_without_matching_output(tmp_path, monkeypatch):
    state_path = tmp_path / "netengine_state.json"
    monkeypatch.setenv("NETENGINE_STATE_FILE", str(state_path))
    state_path.write_text(
        json.dumps(
            {
                "phase_completed": {
                    "0": True,
                    "1": True,
                    "2": True,
                    "4": True,
                    "5": True,
                },
                "substrate_output": {"healthy": True},
                "dns_output": {"healthy": True},
                "identity_platform_output": None,
                "world_registry_output": {"seeded": True},
                "domain_registry_output": None,
            }
        )
    )

    state = RuntimeState.load()

    assert state.phase_completed == {"0": True, "1": True, "2": True}


def test_repeated_saves_with_custom_state_file_are_atomic_and_private(tmp_path, monkeypatch):
    state_path = tmp_path / "custom.state.with.dots.json"
    monkeypatch.setenv("NETENGINE_STATE_FILE", str(state_path))

    state = RuntimeState(correlation_id="first")
    state.save()
    state.correlation_id = "second"
    state.last_error = "saved twice"
    state.save()

    assert state_path.exists()
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))

    loaded = RuntimeState.load()
    assert loaded.correlation_id == "second"
    assert loaded.last_error == "saved twice"


async def test_sync_to_supabase_returns_task_and_logs_async_failure(monkeypatch):
    async def failing_get_db():
        raise RuntimeError("boom")

    debug_messages = []
    monkeypatch.setitem(
        __import__("sys").modules,
        "netengine.core.supabase_client",
        types.SimpleNamespace(get_db=failing_get_db),
    )
    monkeypatch.setattr(
        "netengine.core.state.logger.debug", lambda message: debug_messages.append(message)
    )

    task = RuntimeState().sync_to_supabase()

    assert task is not None
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert task.done()
    assert debug_messages == ["State DB sync skipped: boom"]


def test_redact_for_api_masks_secret_fields():
    value = {
        "admin_password": "super-secret",
        "nested": {"platform_client_secret": "client-secret", "public": "ok"},
    }

    assert redact_for_api(value) == {
        "admin_password": "[REDACTED]",
        "nested": {"platform_client_secret": "[REDACTED]", "public": "ok"},
    }
    assert redact_for_api(value, include_secrets=True) == value


def test_redact_for_support_bundle_drops_secret_fields_and_private_pems():
    value = {
        "admin_password": "super-secret",
        "nested": {
            "platform_client_secret": "client-secret",
            "public": "ok",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        },
    }

    assert redact_for_support_bundle(value) == {"nested": {"public": "ok"}}
