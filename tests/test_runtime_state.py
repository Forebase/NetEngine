import json

from netengine.core.state import RuntimeState


def test_runtime_state_uses_env_path(tmp_path, monkeypatch):
    state_path = tmp_path / "state" / "netengines_state.json"
    monkeypatch.setenv("NETENGINES_STATE_FILE", str(state_path))

    state = RuntimeState()
    state.save()

    assert state_path.exists()
    assert not (tmp_path / "netengines_state.json").exists()


def test_load_discards_completed_phase_without_matching_output(tmp_path, monkeypatch):
    state_path = tmp_path / "netengines_state.json"
    monkeypatch.setenv("NETENGINES_STATE_FILE", str(state_path))
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
