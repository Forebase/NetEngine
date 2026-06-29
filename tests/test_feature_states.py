"""Feature-state metadata tests for spec fields."""

from pathlib import Path

from netengine.spec.feature_state import FEATURE_STATE_REGISTRY
from netengine.spec.models import (
    FEATURE_STATE_JSON_SCHEMA_KEY,
    PKI_FEATURE_STATES,
    FeatureState,
    NetEngineSpec,
    PKIPhase,
)

_SUPPORT_MATRIX = Path(__file__).resolve().parents[1] / "docs" / "spec-alpha-support.md"

PKI_FIELD_NAMES = {
    "pki.intermediate_ca_enabled": "intermediate_ca_enabled",
    "pki.dnssec_enabled": "dnssec_enabled",
    "pki.dnssec_ksk_lifetime_days": "dnssec_ksk_lifetime_days",
    "pki.dnssec_zsk_lifetime_days": "dnssec_zsk_lifetime_days",
    "pki.crl_enabled": "crl_enabled",
    "pki.ocsp_enabled": "ocsp_enabled",
    "pki.rotation_policy": "rotation_policy",
}


def test_required_pki_fields_have_explicit_registry_feature_states() -> None:
    """Every alpha-sensitive PKI field is explicitly tracked in the registry."""
    assert set(PKI_FEATURE_STATES) == set(PKI_FIELD_NAMES)
    assert all(isinstance(state, FeatureState) for state in PKI_FEATURE_STATES.values())


def test_required_pki_fields_expose_feature_state_on_model_fields() -> None:
    """Validation tooling can discover feature states from Pydantic model fields."""
    for dotted_path, field_name in PKI_FIELD_NAMES.items():
        extra = PKIPhase.model_fields[field_name].json_schema_extra
        assert extra is not None
        assert extra[FEATURE_STATE_JSON_SCHEMA_KEY] == PKI_FEATURE_STATES[dotted_path].value


def test_required_pki_fields_expose_feature_state_in_json_schema() -> None:
    """Documentation generators can discover feature states from JSON Schema."""
    pki_schema = NetEngineSpec.model_json_schema()["$defs"]["PKIPhase"]["properties"]

    for dotted_path, field_name in PKI_FIELD_NAMES.items():
        assert (
            pki_schema[field_name][FEATURE_STATE_JSON_SCHEMA_KEY]
            == PKI_FEATURE_STATES[dotted_path].value
        )


def test_every_registry_entry_is_documented_in_support_matrix() -> None:
    """Each gated field must have a row in docs/spec-alpha-support.md.

    Keeps the alpha support contract in lock-step with the validation registry,
    so removing a gate (for v1) forces a deliberate matrix update — mirroring
    the supply-chain CI check that licenses stay documented.
    """
    matrix_text = _SUPPORT_MATRIX.read_text(encoding="utf-8")
    undocumented = [
        entry.path for entry in FEATURE_STATE_REGISTRY if f"`{entry.path}`" not in matrix_text
    ]
    assert not undocumented, (
        "feature_state registry entries missing from docs/spec-alpha-support.md: " f"{undocumented}"
    )
