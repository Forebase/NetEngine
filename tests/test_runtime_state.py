import asyncio
import json
import types

from netengine.core.state import RuntimeState


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
