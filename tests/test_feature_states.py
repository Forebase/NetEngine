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

_DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
_SUPPORT_MATRIXES = (
    _DOCS_DIR / "support-matrix.md",
    _DOCS_DIR / "spec-alpha-support.md",
)

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
    registry_paths = {entry.path for entry in FEATURE_STATE_REGISTRY}

    assert set(PKI_FEATURE_STATES) == set(PKI_FIELD_NAMES)
    assert set(PKI_FIELD_NAMES).issubset(registry_paths)
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


def _support_matrix_rows(path: Path) -> dict[str, dict[str, str]]:
    """Return support-matrix table rows keyed by dotted field path."""
    rows: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        dotted_path = cells[0].strip("`")
        rows[dotted_path] = {
            "state": cells[1].strip("`"),
            "default": cells[2],
            "owner": cells[3],
            "caveat": cells[4],
        }
    return rows


def test_registry_entries_are_documented_consistently_in_support_matrices() -> None:
    """Registry paths must have matching, non-contradictory rows in both support docs.

    Keeps the alpha support contract in lock-step with the validation registry,
    so removing or changing a gate forces a deliberate matrix update.
    """
    expected_states = {entry.path: entry.state for entry in FEATURE_STATE_REGISTRY}

    for matrix_path in _SUPPORT_MATRIXES:
        rows = _support_matrix_rows(matrix_path)
        missing = sorted(set(expected_states) - set(rows))
        assert not missing, f"feature_state registry entries missing from {matrix_path}: {missing}"

        contradictions = {
            path: {"registry": state, "docs": rows[path]["state"]}
            for path, state in expected_states.items()
            if rows[path]["state"] != state
        }
        assert not contradictions, (
            f"feature_state registry entries contradicted by {matrix_path}: {contradictions}"
        )
