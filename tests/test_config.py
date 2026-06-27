"""Tests for configuration management."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from netengine.config.loader import ConfigLoader
from netengine.config.spec_config import SpecConfig
from netengine.spec.loader import (
    SpecLoadError,
    load_spec,
    load_spec_with_composition,
    load_spec_with_environment,
)


@pytest.fixture
def temp_spec_dir() -> Path:
    """Create temporary directory for test specs."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def _minimal_spec(name: str = "test-network") -> dict:
    """Create a minimal valid NetEngine spec."""
    return {
        "metadata": {
            "name": name,
            "version": "1.0",
            "lifecycle": "ephemeral",
        },
        "substrate": {
            "orchestrator": "swarm",
            "ntp": {"enabled": True, "servers": ["pool.ntp.org"]},
            "networks": {
                "platform": {"type": "bridge", "subnet": "172.28.0.0/16"},
                "core": {"type": "bridge", "subnet": "10.0.0.0/24"},
            },
            "gateway": {"platform_ip": "172.28.0.1", "core_ip": "10.0.0.1"},
        },
        "dns": {
            "root": {
                "enabled": True,
                "type": "authoritative",
                "server": "coredns",
                "listen_ip": "10.0.0.2",
                "soa_primary_ns": "root.internal",
                "soa_email": "admin.internal",
                "serial_policy": "timestamp",
            },
            "platform_zone": {
                "name": "platform.internal",
                "type": "authoritative",
                "listen_ip": "10.0.0.3",
            },
            "tlds": [
                {
                    "name": "internal",
                    "type": "authoritative",
                    "listen_ip": "10.0.0.4",
                }
            ],
        },
        "pki": {
            "root_ca": {
                "cn": "Test CA",
                "o": "Test",
                "c": "US",
                "key_storage_mode": "ephemeral",
                "cert_lifetime_days": 3650,
            },
            "acme": {
                "enabled": True,
                "listen_ip": "10.0.0.6",
                "canonical_name": "ca.platform.internal",
            },
            "dnssec_enabled": True,
        },
        "identity_platform": {
            "oidc_provider": "keycloak",
            "listen_ip": "10.0.0.7",
            "canonical_name": "auth.platform.internal",
            "realm_name": "platform",
            "admin_user": {"username": "admin", "email": "admin@test.internal"},
            "scopes": [],
        },
        "world_registry": {
            "enabled": True,
            "listen_ip": "10.0.0.8",
            "canonical_name": "registry.platform.internal",
            "organizations": [],
            "operators": [],
        },
        "domain_registry": {
            "enabled": True,
            "listen_ip": "10.0.0.10",
            "canonical_name": "domainreg.platform.internal",
        },
        "identity_inworld": {
            "oidc_provider": "keycloak",
            "listen_ip": "10.0.0.12",
            "canonical_name": "auth.internal",
            "realm_name": "inworld",
            "org_users": [],
        },
        "ands": {"profiles": {}, "instances": []},
        "world_services": {"mail": {"enabled": False}, "storage": {"enabled": False}},
        "org_apps": {"enabled": True, "catalog": [], "deployments": []},
        "gateway_portal": {
            "enabled": True,
            "real_internet": {"mode": "isolated"},
            "cross_world": {"mode": "none"},
        },
        "operator": {
            "api": {
                "enabled": True,
                "listen_ip": "172.28.0.11",
                "port": 8080,
                "canonical_name": "api.platform.internal",
            },
            "auth": {
                "provider": "oidc",
                "issuer": "https://auth.platform.internal/realms/platform",
                "required_scope": "netengines:read",
            },
        },
    }


@pytest.fixture
def base_spec_file(temp_spec_dir: Path) -> Path:
    """Create a base spec file."""
    spec = _minimal_spec("test-network")
    spec_file = temp_spec_dir / "spec.base.yaml"
    with open(spec_file, "w") as f:
        yaml.dump(spec, f)
    return spec_file


@pytest.fixture
def prod_spec_file(temp_spec_dir: Path) -> Path:
    """Create a production override spec file."""
    spec = {
        "substrate": {
            "gateway": {"platform_ip": "172.28.0.1", "core_ip": "10.0.0.1"},
        },
    }
    spec_file = temp_spec_dir / "spec.prod.yaml"
    with open(spec_file, "w") as f:
        yaml.dump(spec, f)
    return spec_file


@pytest.fixture
def dev_spec_file(temp_spec_dir: Path) -> Path:
    """Create a development override spec file."""
    spec = {
        "metadata": {
            "environment": "dev",
            "name": "test-network",
        },
    }
    spec_file = temp_spec_dir / "spec.dev.yaml"
    with open(spec_file, "w") as f:
        yaml.dump(spec, f)
    return spec_file


class TestConfigLoader:
    """Tests for ConfigLoader utilities."""

    def test_load_yaml(self, base_spec_file: Path) -> None:
        """Test loading YAML file."""
        data = ConfigLoader.load_yaml(base_spec_file)
        assert data["metadata"]["name"] == "test-network"

    def test_merge_configs(self) -> None:
        """Test merging multiple configurations."""
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"c": 3, "d": 4}}
        merged = ConfigLoader.merge_configs(base, override)

        assert merged["a"] == 1
        assert merged["b"]["c"] == 3
        assert merged["b"]["d"] == 4

    def test_merge_multiple_configs(self) -> None:
        """Test merging three or more configurations."""
        config1 = {"a": 1, "b": 2}
        config2 = {"b": 3, "c": 4}
        config3 = {"c": 5, "d": 6}

        merged = ConfigLoader.merge_configs(config1, config2, config3)

        assert merged["a"] == 1
        assert merged["b"] == 3
        assert merged["c"] == 5
        assert merged["d"] == 6


class TestSpecConfig:
    """Tests for spec configuration loading."""

    def test_load_spec(self, base_spec_file: Path) -> None:
        """Test loading a spec file."""
        spec = SpecConfig.load(base_spec_file)
        assert spec["metadata"]["name"] == "test-network"

    def test_load_with_base(self, base_spec_file: Path, prod_spec_file: Path) -> None:
        """Test loading spec with base composition."""
        spec = SpecConfig.load(prod_spec_file, base_path=base_spec_file)
        assert spec["metadata"]["name"] == "test-network"

    def test_load_with_overrides(self, base_spec_file: Path) -> None:
        """Test loading spec with inline overrides."""
        overrides = {"metadata": {"environment": "staging"}}
        spec = SpecConfig.load(base_spec_file, overrides=overrides)

        assert spec["metadata"]["environment"] == "staging"
        assert spec["metadata"]["name"] == "test-network"  # from base

    def test_load_environment_variants_dev(self, base_spec_file: Path, dev_spec_file: Path) -> None:
        """Test loading base spec with dev environment overrides."""
        spec = SpecConfig.load_environment_variants(base_spec_file, environment="dev")

        assert spec["metadata"]["name"] == "test-network"
        # Dev spec sets environment field
        assert spec["metadata"].get("environment") == "dev"

    def test_load_environment_variants_prod(
        self, base_spec_file: Path, prod_spec_file: Path
    ) -> None:
        """Test loading base spec with prod environment overrides."""
        spec = SpecConfig.load_environment_variants(base_spec_file, environment="prod")

        assert spec["metadata"]["name"] == "test-network"

    def test_load_environment_variants_missing(self, base_spec_file: Path) -> None:
        """Test loading base spec when environment variant doesn't exist."""
        spec = SpecConfig.load_environment_variants(base_spec_file, environment="staging")

        # Should load base spec only, environment file doesn't exist
        assert spec["metadata"]["name"] == "test-network"

    def test_load_environment_with_overrides(
        self, base_spec_file: Path, dev_spec_file: Path
    ) -> None:
        """Test environment loading with additional overrides."""
        overrides = {"metadata": {"environment": "prod"}}
        spec = SpecConfig.load_environment_variants(
            base_spec_file, environment="dev", overrides=overrides
        )

        assert spec["metadata"]["environment"] == "prod"


class TestSpecLoaderIntegration:
    """Integration tests for spec loader with OmegaConf."""

    def test_load_spec_backward_compatibility(self, base_spec_file: Path) -> None:
        """Test that original load_spec function still works."""
        spec = load_spec(base_spec_file)
        assert spec.metadata.name == "test-network"

    def test_load_spec_with_composition(self, base_spec_file: Path, prod_spec_file: Path) -> None:
        """Test load_spec_with_composition function."""
        spec = load_spec_with_composition(prod_spec_file, base_path=base_spec_file)

        assert spec.metadata.name == "test-network"

    def test_load_spec_with_environment(self, base_spec_file: Path, dev_spec_file: Path) -> None:
        """Test load_spec_with_environment function."""
        spec = load_spec_with_environment(base_spec_file, environment="dev")

        assert spec.metadata.name == "test-network"
        # Dev spec sets environment field if present
        if spec.metadata.environment is not None:
            assert spec.metadata.environment == "dev"

    def test_spec_not_found(self, temp_spec_dir: Path) -> None:
        """Test error when spec file not found."""
        with pytest.raises(SpecLoadError, match="not found"):
            load_spec(temp_spec_dir / "nonexistent.yaml")

    def test_spec_invalid_yaml(self, temp_spec_dir: Path) -> None:
        """Test error on invalid YAML."""
        spec_file = temp_spec_dir / "invalid.yaml"
        with open(spec_file, "w") as f:
            f.write("invalid: yaml: content: [")

        with pytest.raises(SpecLoadError, match="Failed to parse YAML"):
            load_spec(spec_file)


class TestSpecCrossValidation:
    """Tests for cross-field spec validation (CIDR overlap, name uniqueness, etc.)."""

    def _write_spec(self, tmp_dir: Path, spec: dict) -> Path:
        path = tmp_dir / "spec.yaml"
        with open(path, "w") as f:
            yaml.dump(spec, f)
        return path

    def test_valid_spec_passes(self, temp_spec_dir: Path) -> None:
        path = self._write_spec(temp_spec_dir, _minimal_spec())
        spec = load_spec(path)
        assert spec.metadata.name == "test-network"

    def test_overlapping_subnets_rejected(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        # Both networks overlap: 10.0.0.0/8 contains 10.1.0.0/16
        data["substrate"]["networks"] = {
            "core": {"type": "bridge", "subnet": "10.0.0.0/8"},
            "extra": {"type": "bridge", "subnet": "10.1.0.0/16"},
        }
        path = self._write_spec(temp_spec_dir, data)
        with pytest.raises(SpecLoadError, match="subnet overlap"):
            load_spec(path)

    def test_non_overlapping_subnets_accepted(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        data["substrate"]["networks"] = {
            "platform": {"type": "bridge", "subnet": "172.28.0.0/16"},
            "core": {"type": "bridge", "subnet": "10.0.0.0/24"},
        }
        path = self._write_spec(temp_spec_dir, data)
        load_spec(path)  # should not raise

    def test_invalid_cidr_rejected(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        data["substrate"]["networks"]["bad"] = {"type": "bridge", "subnet": "not-a-cidr"}
        path = self._write_spec(temp_spec_dir, data)
        with pytest.raises(SpecLoadError, match="invalid CIDR"):
            load_spec(path)

    def test_duplicate_and_instance_names_rejected(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        data["ands"]["instances"] = [
            {"name": "office", "org": "acme", "profile": "business", "dns_suffix": "office.acme"},
            {"name": "office", "org": "acme", "profile": "residential", "dns_suffix": "home.acme"},
        ]
        path = self._write_spec(temp_spec_dir, data)
        with pytest.raises(SpecLoadError, match="Duplicate AND instance name"):
            load_spec(path)

    def test_unique_and_instance_names_accepted(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        data["ands"]["instances"] = [
            {"name": "office", "org": "acme", "profile": "business", "dns_suffix": "office.acme"},
            {"name": "home", "org": "acme", "profile": "residential", "dns_suffix": "home.acme"},
        ]
        path = self._write_spec(temp_spec_dir, data)
        load_spec(path)  # should not raise

    def test_unknown_org_in_and_instance_rejected(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        data["world_registry"]["organizations"] = [{"name": "acme"}]
        data["ands"]["instances"] = [
            {"name": "office", "org": "unknown-org", "profile": "business", "dns_suffix": "o.u"},
        ]
        path = self._write_spec(temp_spec_dir, data)
        with pytest.raises(SpecLoadError, match="unknown org"):
            load_spec(path)

    def test_unknown_profile_in_and_instance_rejected(self, temp_spec_dir: Path) -> None:
        data = _minimal_spec()
        data["ands"]["profiles"] = {"business": {"type": "business", "description": "biz"}}
        data["ands"]["instances"] = [
            {"name": "office", "org": "acme", "profile": "nonexistent", "dns_suffix": "o.a"},
        ]
        path = self._write_spec(temp_spec_dir, data)
        with pytest.raises(SpecLoadError, match="unknown profile"):
            load_spec(path)
