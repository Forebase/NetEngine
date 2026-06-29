from netengine.diagnostic.probes import pki
from netengine.diagnostic.runner import ProbeResult, ProbeStatus, _apply_probe_defaults, _probe_name


def test_probe_result_carries_actionable_metadata_from_probe_defaults():
    result = ProbeResult(
        name="PKI",
        status=ProbeStatus.FAIL,
        detail="step-ca unreachable",
        hint="Check step-ca container logs.",
    )

    _apply_probe_defaults(result, pki.probe)

    assert result.status == ProbeStatus.FAIL
    assert result.remediation == "Check step-ca container logs."
    assert result.related_phase == 3
    assert result.related_resource == "step-ca / CA certificate"
    assert result.related_logs == ["docker logs netengines_step_ca"]
    assert result.command_to_retry == "netengine heal --phase 3"
    assert result.phase == 3
    assert result.resource == "step-ca / CA certificate"
    assert result.logs == ["docker logs netengines_step_ca"]
    assert result.retry_command == "netengine heal --phase 3"


def test_probe_name_uses_module_display_name_for_crashes():
    assert _probe_name(pki.probe) == "PKI"


def test_probe_result_accepts_legacy_actionable_field_names():
    result = ProbeResult(
        name="PKI",
        status=ProbeStatus.FAIL,
        detail="step-ca unreachable",
        phase=3,
        resource="step-ca",
        logs=["docker logs netengines_step_ca"],
        retry_command="netengine heal --phase 3",
    )

    assert result.related_phase == 3
    assert result.related_resource == "step-ca"
    assert result.related_logs == ["docker logs netengines_step_ca"]
    assert result.command_to_retry == "netengine heal --phase 3"
