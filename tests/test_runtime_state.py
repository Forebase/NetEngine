import json
import stat

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
