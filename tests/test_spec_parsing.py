"""M0 Validation: Spec parsing and immutability tests.

Definition of Done for M0:
1. All three example specs parse without error
2. Parsed specs are immutable (frozen)
3. Spec validation rejects invalid input
"""

import pytest
from pydantic import ValidationError

from netengine.spec.loader import SpecLoadError, load_spec
from netengine.spec.models import NetEngineSpec


class TestSpecParsing:
    """Spec parsing tests."""

    def test_minimal_spec_parses(self, minimal_spec: NetEngineSpec) -> None:
        """Minimal spec should parse without error."""
        assert minimal_spec is not None
        assert minimal_spec.metadata.name == "minimal-example"
        assert minimal_spec.metadata.version == "1.0"

    def test_single_org_spec_parses(self, single_org_spec: NetEngineSpec) -> None:
        """Single-org spec should parse without error."""
        assert single_org_spec is not None
        assert single_org_spec.metadata.name == "single-org"
        assert single_org_spec.metadata.organization == "acme-corp"
        assert len(single_org_spec.world_registry.organizations) > 0

    def test_dev_sandbox_spec_parses(self, dev_sandbox_spec: NetEngineSpec) -> None:
        """Dev-sandbox spec should parse without error."""
        assert dev_sandbox_spec is not None
        assert dev_sandbox_spec.metadata.name == "dev-sandbox"
        assert dev_sandbox_spec.metadata.environment == "development"

    def test_all_specs_have_required_fields(self, minimal_spec: NetEngineSpec) -> None:
        """All specs should have all required top-level sections."""
        assert minimal_spec.metadata is not None
        assert minimal_spec.substrate is not None
        assert minimal_spec.dns is not None
        assert minimal_spec.pki is not None
        assert minimal_spec.identity_platform is not None
        assert minimal_spec.world_registry is not None
        assert minimal_spec.domain_registry is not None
        assert minimal_spec.identity_inworld is not None
        assert minimal_spec.ands is not None
        assert minimal_spec.world_services is not None
        assert minimal_spec.org_apps is not None
        assert minimal_spec.gateway_portal is not None
        assert minimal_spec.operator is not None


class TestSpecImmutability:
    """Spec immutability tests (frozen dataclasses)."""

    def test_spec_is_immutable(self, minimal_spec: NetEngineSpec) -> None:
        """Spec should be frozen after parsing."""
        with pytest.raises(ValidationError):
            # Pydantic v2 raises ValidationError on frozen model modification
            minimal_spec.metadata.name = "modified"

    def test_spec_metadata_immutable(self, minimal_spec: NetEngineSpec) -> None:
        """Spec metadata should be immutable."""
        with pytest.raises(ValidationError):
            minimal_spec.metadata.version = "2.0"

    def test_spec_substrate_immutable(self, minimal_spec: NetEngineSpec) -> None:
        """Substrate config should be immutable."""
        original_subnet = minimal_spec.substrate.networks["platform"].subnet
        with pytest.raises(ValidationError):
            minimal_spec.substrate.networks["platform"].subnet = "192.168.0.0/16"
        # Verify it didn't change
        assert minimal_spec.substrate.networks["platform"].subnet == original_subnet

    def test_spec_dns_immutable(self, minimal_spec: NetEngineSpec) -> None:
        """DNS config should be immutable."""
        original_ip = minimal_spec.dns.root.listen_ip
        with pytest.raises(ValidationError):
            minimal_spec.dns.root.listen_ip = "10.0.0.99"
        assert minimal_spec.dns.root.listen_ip == original_ip


class TestSpecLoading:
    """Spec loading and validation error handling."""

    def test_load_spec_file_not_found(self) -> None:
        """Loading nonexistent file should raise SpecLoadError."""
        with pytest.raises(SpecLoadError, match="not found"):
            load_spec("/nonexistent/spec.yaml")

    def test_load_spec_invalid_yaml(self, tmp_path) -> None:
        """Invalid YAML should raise SpecLoadError."""
        spec_file = tmp_path / "invalid.yaml"
        spec_file.write_text("{\n  invalid: yaml: syntax:\n")
        with pytest.raises(SpecLoadError, match="Failed to parse YAML"):
            load_spec(spec_file)

    def test_load_spec_not_dict(self, tmp_path) -> None:
        """YAML that is not an object should raise SpecLoadError."""
        spec_file = tmp_path / "not_dict.yaml"
        spec_file.write_text("- just\n- a\n- list\n")
        with pytest.raises(SpecLoadError, match="must be a YAML object"):
            load_spec(spec_file)

    def test_load_spec_missing_required_field(self, tmp_path) -> None:
        """Spec missing required fields should raise SpecLoadError."""
        spec_file = tmp_path / "incomplete.yaml"
        # Minimal valid spec has all required fields, so test an empty one
        spec_file.write_text("metadata:\n  name: test\n")
        with pytest.raises(SpecLoadError, match="validation failed"):
            load_spec(spec_file)


class TestSpecDefaults:
    """Test that spec defaults are applied correctly."""

    def test_minimal_spec_has_defaults(self, minimal_spec: NetEngineSpec) -> None:
        """Minimal spec should have sensible defaults."""
        assert minimal_spec.metadata.lifecycle.value == "ephemeral"
        assert minimal_spec.substrate.orchestrator.value == "swarm"
        assert minimal_spec.dns.root.listen_ip == "10.0.0.2"
        assert minimal_spec.pki.root_ca.cert_lifetime_days == 3650
        assert minimal_spec.pki.dnssec_enabled is False
        assert minimal_spec.gateway_portal.enabled is True

    def test_networks_defaults(self, minimal_spec: NetEngineSpec) -> None:
        """Substrate networks should have default values."""
        assert "platform" in minimal_spec.substrate.networks
        assert "core" in minimal_spec.substrate.networks
        assert minimal_spec.substrate.networks["platform"].subnet == "172.28.0.0/16"
        assert minimal_spec.substrate.networks["core"].subnet == "10.0.0.0/24"

    def test_tld_defaults(self, single_org_spec: NetEngineSpec) -> None:
        """TLDs should have defaults if present."""
        if single_org_spec.dns.tlds:
            tld = single_org_spec.dns.tlds[0]
            assert tld.type == "authoritative"
            assert tld.listen_ip is not None


class TestFeatureStateValidation:
    """Feature-state validation rejects unsupported fields and warns on experiments."""

    def _write_spec(self, tmp_path, overrides: dict):
        from pathlib import Path

        import yaml

        base = yaml.safe_load(
            (Path(__file__).parent.parent / "examples" / "minimal.yaml").read_text()
        )

        def _merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    _merge(a[k], v)
                else:
                    a[k] = v

        _merge(base, overrides)
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.safe_dump(base))
        return spec_file

    def test_experimental_pki_field_logs_warning_not_error(self, tmp_path, caplog) -> None:
        import logging

        # DNSSEC/CRL/OCSP were promoted from unsupported to experimental once the
        # CoreDNS online-signing + step-ca config paths were wired (WS-A). They
        # now load with a warning instead of being rejected.
        spec_file = self._write_spec(tmp_path, {"pki": {"dnssec_enabled": True}})

        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)

        assert spec.pki.dnssec_enabled is True
        assert any(
            "pki.dnssec_enabled is experimental in alpha" in r.message for r in caplog.records
        )

    def test_unsupported_non_default_active_field_raises_spec_load_error(self, tmp_path) -> None:
        spec_file = self._write_spec(
            tmp_path, {"gateway_portal": {"real_internet": {"mode": "mirrored"}}}
        )

        with pytest.raises(
            SpecLoadError,
            match="gateway_portal\\.real_internet\\.mode is unsupported in alpha",
        ):
            load_spec(spec_file)

    def test_unsupported_profile_field_includes_dotted_path(self, tmp_path) -> None:
        spec_file = self._write_spec(
            tmp_path,
            {
                "ands": {
                    "profiles": {
                        "branch": {
                            "dhcp": True,
                            "nat": True,
                            "dynamic_ip": False,
                            "reverse_dns": True,
                            "inbound": "blocked",
                        }
                    }
                }
            },
        )

        with pytest.raises(
            SpecLoadError,
            match="ands\\.profiles\\.branch\\.reverse_dns is unsupported in alpha",
        ):
            load_spec(spec_file)

    def test_experimental_enabled_field_logs_warning(self, tmp_path, caplog) -> None:
        import logging

        spec_file = self._write_spec(tmp_path, {"pki": {"intermediate_ca_enabled": True}})

        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            spec = load_spec(spec_file)

        assert spec.pki.intermediate_ca_enabled is True
        assert any(
            "pki.intermediate_ca_enabled is experimental in alpha" in r.message
            for r in caplog.records
        )

    def test_no_warnings_for_default_spec(self, caplog, minimal_spec) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="netengine.spec.loader"):
            from netengine.spec.loader import _validate_feature_states

            _validate_feature_states(minimal_spec)
        assert minimal_spec.pki.dnssec_enabled is False
        assert not caplog.records
