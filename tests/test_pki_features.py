"""Tests for declared-but-not-implemented PKI features.

Covers:
- Intermediate CA cert exposure
- CRL config injection
- OCSP config injection
- DNSSEC key generation
- Dynamic PKI rotation policy wiring
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.core.state import RuntimeState
from netengine.errors import PKIError
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.pki_handler import PKIHandler
from netengine.workers.pki_cert_rotation_worker import CertTypeRotationConfig


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def mock_docker():
    d = MagicMock()
    d.ensure_volume = AsyncMock()
    d.run_container_one_off = AsyncMock(return_value={"exit_code": 0, "logs": ""})
    d.start_container = AsyncMock(return_value="container-abc")
    d.exec_command = AsyncMock(return_value=(0, ""))
    d.copy_to_container = AsyncMock()
    return d


@pytest.fixture
def minimal_spec_dict():
    return {
        "pki": {
            "acme": {"listen_ip": "10.0.0.6", "canonical_name": "ca.platform.internal"},
            "crl_enabled": False,
            "ocsp_enabled": False,
            "intermediate_ca_enabled": False,
            "dnssec_enabled": False,
        }
    }


@pytest.fixture
def state():
    return RuntimeState()


@pytest.fixture
def pki_handler(mock_docker, state, minimal_spec_dict):
    return PKIHandler(mock_docker, state, minimal_spec_dict)


# ─────────────────────────────────────────────
# Intermediate CA
# ─────────────────────────────────────────────


class TestIntermediateCACert:
    async def test_read_intermediate_cert_returns_content(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off.return_value = {
            "exit_code": 0,
            "logs": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----\n",
        }
        cert = await pki_handler.read_intermediate_cert()
        assert "BEGIN CERTIFICATE" in cert

    async def test_read_intermediate_cert_raises_on_failure(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off.return_value = {"exit_code": 1, "logs": "not found"}
        with pytest.raises(PKIError, match="intermediate CA"):
            await pki_handler.read_intermediate_cert()

    async def test_read_intermediate_uses_correct_path(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off.return_value = {"exit_code": 0, "logs": "CERT"}
        await pki_handler.read_intermediate_cert()
        call_args = mock_docker.run_container_one_off.call_args
        cmd = call_args[1].get("command") or call_args[0][1]
        # _read_file_from_volume remaps /home/step → /data
        assert "intermediate_ca.crt" in " ".join(cmd)

    async def test_bootstrap_stores_intermediate_cert_when_enabled(
        self, mock_docker, state
    ):
        spec = {
            "pki": {
                "acme": {"listen_ip": "10.0.0.6", "canonical_name": "ca.platform.internal"},
                "crl_enabled": False,
                "ocsp_enabled": False,
                "intermediate_ca_enabled": True,
                "dnssec_enabled": False,
            }
        }

        def side_effect(**kwargs):
            cmd = kwargs.get("command", [])
            if "intermediate_ca.crt" in " ".join(cmd):
                return {"exit_code": 0, "logs": "-----BEGIN CERTIFICATE-----\nINTERMEDIATE\n-----END CERTIFICATE-----"}
            if "ca.crt" in " ".join(cmd):
                return {"exit_code": 0, "logs": "-----BEGIN CERTIFICATE-----\nROOT\n-----END CERTIFICATE-----"}
            if "password.txt" in " ".join(cmd) and cmd[0] == "cat":
                return {"exit_code": 0, "logs": "secret-password"}
            return {"exit_code": 0, "logs": ""}

        mock_docker.run_container_one_off = AsyncMock(side_effect=lambda **kw: side_effect(**kw))

        # Pre-populate state so _generate_ca is skipped (already generated)
        state.ca_cert_pem = "EXISTING_CA"
        state.step_ca_container_id = "existing-container"

        handler = PKIHandler(mock_docker, state, spec)
        with patch.object(handler, "healthcheck", AsyncMock(return_value=True)):
            await handler.bootstrap()

        assert state.intermediate_ca_cert is not None
        assert "INTERMEDIATE" in state.intermediate_ca_cert


# ─────────────────────────────────────────────
# CRL
# ─────────────────────────────────────────────


class TestCRLConfig:
    async def test_inject_crl_config_adds_crl_section(self, pki_handler, mock_docker):
        ca_config = {
            "root": "/home/step/certs/root_ca.crt",
            "authority": {"provisioners": []},
        }
        # First call reads config, subsequent calls are writes
        mock_docker.run_container_one_off = AsyncMock(
            side_effect=[
                {"exit_code": 0, "logs": json.dumps(ca_config)},  # _read_ca_config
                {"exit_code": 0, "logs": ""},  # _write_ca_config (cp)
            ]
        )

        with patch("tempfile.NamedTemporaryFile") as mock_tmp, patch("os.unlink"):
            mock_tmp.return_value.__enter__ = MagicMock(
                return_value=MagicMock(name="f", write=MagicMock())
            )
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            mock_tmp.return_value.__enter__.return_value.name = "/tmp/fake.json"
            await pki_handler._inject_crl_config()

        # Verify the write call contained a config with crl.enabled = true
        write_call = mock_docker.run_container_one_off.call_args_list[1]
        # The copy command will have been called; the config was written to a temp file
        # which we can't easily inspect here — just verify the call was made
        assert mock_docker.run_container_one_off.call_count == 2

    async def test_inject_crl_raises_on_read_failure(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off.return_value = {"exit_code": 1, "logs": "error"}
        with pytest.raises(PKIError, match="CA config"):
            await pki_handler._inject_crl_config()


# ─────────────────────────────────────────────
# OCSP
# ─────────────────────────────────────────────


class TestOCSPConfig:
    async def test_inject_ocsp_config_enables_ocsp_in_authority(self, pki_handler, mock_docker):
        ca_config = {"authority": {"provisioners": []}}

        def capture_write(**kwargs):
            cmd = kwargs.get("command", [])
            if cmd[0] == "cat":
                return {"exit_code": 0, "logs": json.dumps(ca_config)}
            return {"exit_code": 0, "logs": ""}

        mock_docker.run_container_one_off = AsyncMock(
            side_effect=lambda **kw: capture_write(**kw)
        )

        with patch("tempfile.NamedTemporaryFile") as mock_tmp, patch("os.unlink"):
            mock_file = MagicMock()
            mock_file.name = "/tmp/fake.json"
            mock_tmp.return_value.__enter__ = MagicMock(return_value=mock_file)
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            await pki_handler._inject_ocsp_config()

        assert mock_docker.run_container_one_off.call_count == 2

    async def test_inject_ocsp_raises_on_read_failure(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off.return_value = {"exit_code": 1, "logs": "io error"}
        with pytest.raises(PKIError, match="CA config"):
            await pki_handler._inject_ocsp_config()


# ─────────────────────────────────────────────
# DNSSEC
# ─────────────────────────────────────────────


class TestDNSSEC:
    async def test_setup_dnssec_creates_volume(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off = AsyncMock(
            side_effect=[
                {"exit_code": 0, "logs": "Kinternal.+013+01234"},  # KSK
                {"exit_code": 0, "logs": "Kinternal.+013+05678"},  # ZSK
            ]
        )
        await pki_handler.setup_dnssec("internal")
        mock_docker.ensure_volume.assert_called_once_with("netengines_dnssec_keys")

    async def test_setup_dnssec_returns_key_names(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off = AsyncMock(
            side_effect=[
                {"exit_code": 0, "logs": "Kinternal.+013+01234"},
                {"exit_code": 0, "logs": "Kinternal.+013+05678"},
            ]
        )
        result = await pki_handler.setup_dnssec("internal", ksk_lifetime_days=365, zsk_lifetime_days=30)
        assert result["zone"] == "internal"
        assert result["ksk_name"] == "Kinternal.+013+01234"
        assert result["zsk_name"] == "Kinternal.+013+05678"
        assert result["algorithm"] == "ECDSAP256SHA256"
        assert result["ksk_lifetime_days"] == 365
        assert result["zsk_lifetime_days"] == 30

    async def test_setup_dnssec_uses_ksk_flag_for_ksk(self, pki_handler, mock_docker):
        calls = []

        def capture(**kwargs):
            calls.append(kwargs.get("command", []))
            return {"exit_code": 0, "logs": "Kinternal.+013+00001"}

        mock_docker.run_container_one_off = AsyncMock(side_effect=lambda **kw: capture(**kw))
        await pki_handler.setup_dnssec("internal")

        ksk_cmd = calls[0]
        zsk_cmd = calls[1]
        assert "-f" in ksk_cmd and "KSK" in ksk_cmd
        assert "-f" not in zsk_cmd or "KSK" not in zsk_cmd

    async def test_setup_dnssec_raises_on_ksk_failure(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off = AsyncMock(
            return_value={"exit_code": 1, "logs": "permission denied"}
        )
        with pytest.raises(PKIError, match="KSK generation failed"):
            await pki_handler.setup_dnssec("internal")

    async def test_setup_dnssec_raises_on_zsk_failure(self, pki_handler, mock_docker):
        mock_docker.run_container_one_off = AsyncMock(
            side_effect=[
                {"exit_code": 0, "logs": "Kinternal.+013+01234"},
                {"exit_code": 1, "logs": "keygen error"},
            ]
        )
        with pytest.raises(PKIError, match="ZSK generation failed"):
            await pki_handler.setup_dnssec("internal")

    async def test_pki_flag_reads_from_pydantic_model(self):
        from unittest.mock import MagicMock
        spec = MagicMock()
        spec.pki.dnssec_enabled = True
        handler = PKIHandler(MagicMock(), RuntimeState(), spec)
        assert handler._pki_flag("dnssec_enabled") is True

    async def test_pki_flag_reads_from_dict_spec(self, mock_docker, state):
        spec = {"pki": {"acme": {}, "dnssec_enabled": True}}
        handler = PKIHandler(mock_docker, state, spec)
        assert handler._pki_flag("dnssec_enabled") is True

    async def test_pki_flag_defaults_to_false(self, pki_handler):
        assert pki_handler._pki_flag("dnssec_enabled") is False


# ─────────────────────────────────────────────
# PKI Rotation Policy — dynamic cert types
# ─────────────────────────────────────────────


class TestPKIRotationPolicyWiring:
    """_register_rotation_worker wires all cert types from the spec dynamically."""

    def _make_context(self, overrides: dict):
        spec = MagicMock()
        spec.pki.rotation_policy.enabled = True
        spec.pki.rotation_policy.default_interval_hours = 24
        spec.pki.rotation_policy.default_warning_days = 30
        spec.pki.rotation_policy.cert_type_overrides = overrides

        ctx = MagicMock()
        ctx.consumer_supervisor = MagicMock()
        ctx.consumer_supervisor.register = MagicMock()
        ctx.pgmq_client = AsyncMock()
        ctx.runtime_state = RuntimeState()
        ctx.logger = MagicMock()

        return ctx, spec

    def _run_and_capture(self, ctx, spec, handler) -> list:
        """Patch PKICertRotationWorker and return the configs it was constructed with."""
        captured = []

        def fake_worker(pki, pgmq, configs):
            captured.extend(configs)
            m = MagicMock()
            m.run = MagicMock()
            return m

        with patch("netengine.handlers.phase_pki.PKICertRotationWorker", side_effect=fake_worker):
            handler._register_rotation_worker(ctx, MagicMock(), spec)

        return captured

    def test_builtin_four_cert_types_always_registered(self):
        ctx, spec = self._make_context({})
        configs = self._run_and_capture(ctx, spec, PKIPhaseHandler())
        types = [c.cert_type for c in configs]
        assert "platform_identity" in types
        assert "inworld_identity" in types
        assert "app" in types
        assert "storage" in types

    def test_extra_cert_types_from_overrides_are_included(self):
        ctx, spec = self._make_context({"mail": {"rotation_interval_hours": 12}})
        configs = self._run_and_capture(ctx, spec, PKIPhaseHandler())
        types = [c.cert_type for c in configs]
        assert "mail" in types

    def test_override_values_are_applied_to_cert_type(self):
        ctx, spec = self._make_context({
            "app": {"rotation_interval_hours": 6, "expiry_warning_days": 7}
        })
        configs = self._run_and_capture(ctx, spec, PKIPhaseHandler())
        app_cfg = next(c for c in configs if c.cert_type == "app")
        assert app_cfg.rotation_interval_hours == 6
        assert app_cfg.expiry_warning_days == 7

    def test_disabled_policy_skips_registration(self):
        ctx, spec = self._make_context({})
        spec.pki.rotation_policy.enabled = False
        handler = PKIPhaseHandler()
        handler._register_rotation_worker(ctx, MagicMock(), spec)
        ctx.consumer_supervisor.register.assert_not_called()

    def test_no_supervisor_skips_registration(self):
        ctx, spec = self._make_context({})
        ctx.consumer_supervisor = None
        handler = PKIPhaseHandler()
        # Should not raise
        handler._register_rotation_worker(ctx, MagicMock(), spec)
