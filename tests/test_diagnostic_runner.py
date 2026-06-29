from netengine.diagnostic.runner import ProbeResult, ProbeStatus, _apply_probe_defaults

_PHASE = 3
_RESOURCE = "step-ca / CA certificate"
_LOGS = ["docker logs netengines_step_ca"]
_RETRY = "netengine heal --phase 3"


async def dummy_probe(spec):
    return ProbeResult(
        name="PKI",
        status=ProbeStatus.FAIL,
        detail="step-ca unreachable",
        hint="Check step-ca container logs.",
    )


def test_probe_result_carries_actionable_metadata_from_probe_defaults():
    result = ProbeResult(
        name="PKI",
        status=ProbeStatus.FAIL,
        detail="step-ca unreachable",
        hint="Check step-ca container logs.",
    )

    _apply_probe_defaults(result, dummy_probe)

    assert result.status == ProbeStatus.FAIL
    assert result.remediation == "Check step-ca container logs."
    assert result.phase == 3
    assert result.resource == "step-ca / CA certificate"
    assert result.logs == ["docker logs netengines_step_ca"]
    assert result.retry_command == "netengine heal --phase 3"
