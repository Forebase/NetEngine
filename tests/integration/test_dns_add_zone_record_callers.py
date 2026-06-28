"""Regression tests for Phase 3+ DNS record insertion callers."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from netengine.handlers.minio_handler import StorageHandler
from netengine.handlers.phase_pki import PKIPhaseHandler


@pytest.fixture
def context_with_zone_files(phase_context):
    phase_context.runtime_state.dns_output = {
        "zone_files": {
            "platform.internal": "$ORIGIN platform.internal.\n@ 300 IN SOA ns.platform.internal. admin.platform.internal. 1 3600 600 86400 300\n",
            "internal": "$ORIGIN internal.\n@ 300 IN SOA ns.internal. admin.internal. 1 3600 600 86400 300\n",
        }
    }
    phase_context.runtime_state.save = MagicMock()
    return phase_context


@pytest.mark.asyncio
async def test_phase_3_pki_inserts_ca_dns_record(context_with_zone_files):
    """Phase 3 should pass PhaseContext into DNSHandler.add_zone_record."""
    pki = SimpleNamespace(
        ca_ip="10.0.0.6",
        ca_dns="ca.platform.internal",
        bootstrap=AsyncMock(),
        setup_dnssec=AsyncMock(
            return_value={
                "zone": "internal",
                "ksk_name": "Kinternal.+013+00001",
                "zsk_name": "Kinternal.+013+00002",
                "volume": "netengines_dnssec_keys",
                "algorithm": "ECDSAP256SHA256",
                "ksk_lifetime_days": 365,
                "zsk_lifetime_days": 30,
            }
        ),
    )

    with (
        patch("netengine.handlers.phase_pki.PKIHandler", return_value=pki),
        patch("netengine.handlers.phase_pki.DockerHandler"),
    ):
        await PKIPhaseHandler().execute(context_with_zone_files)

    platform_zone = context_with_zone_files.runtime_state.dns_output["zone_files"][
        "platform.internal"
    ]
    assert "ca 300 IN A 10.0.0.6" in platform_zone


@pytest.mark.asyncio
async def test_storage_handler_inserts_minio_dns_record(context_with_zone_files, tmp_path):
    """Phase 8 storage helper should store context and insert DNS records."""
    docker = SimpleNamespace(start_container=AsyncMock())
    future_expiry = datetime.utcnow() + timedelta(days=365)
    pki = SimpleNamespace(
        issue_cert=AsyncMock(return_value=("cert", "key")),
        extract_cert_expiry=MagicMock(return_value=future_expiry),
    )
    dns = __import__("netengine.handlers.dns", fromlist=["DNSHandler"]).DNSHandler()

    handler = StorageHandler(
        context_with_zone_files, docker, dns, pki, context_with_zone_files.runtime_state
    )
    handler._create_bucket = AsyncMock()

    with patch("os.makedirs"), patch("builtins.open", mock_open()):
        await handler.deploy_minio()

    platform_zone = context_with_zone_files.runtime_state.dns_output["zone_files"][
        "platform.internal"
    ]
    assert "storage 300 IN A 10.0.0.14" in platform_zone
