from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from netengine.handlers.app_handler import AppCatalog, AppHandler, OrgAppsPhaseHandler
from netengine.handlers.context import PhaseContext, RuntimeState
from netengine.logs import get_logger
from netengine.spec.models import AppCatalogEntry as SpecCatalogEntry
from netengine.spec.models import AppDeployment, IdentityInWorldPhase, OrgAppsPhase


class Result:
    data = [{"cidr": "10.42.0.0/24"}]


def mock_db():
    db = MagicMock()
    table = MagicMock()
    db.table.return_value = table
    table.upsert.return_value.execute = AsyncMock(return_value=SimpleNamespace(data=[]))
    table.select.return_value.eq.return_value.execute = AsyncMock(return_value=Result())
    return db


def context(tmp_path):
    state = RuntimeState()
    state.identity_inworld_output = {
        "issuer": "https://auth.acme.internal/realms/inworld",
        "realm_name": "inworld",
        "admin_username": "kc-admin",
        "admin_password": "from-state",
    }
    spec = SimpleNamespace(
        identity_inworld=IdentityInWorldPhase(canonical_name="auth.internal", realm_name="inworld"),
        org_apps=OrgAppsPhase(
            catalog=[
                SpecCatalogEntry(name="demo", image="demo:1", port=8080, oidc_integration=True)
            ],
            deployments=[AppDeployment(org="acme", app="demo", subdomain="demo")],
        ),
    )
    return PhaseContext(
        spec=spec, runtime_state=state, logger=get_logger("test"), docker_client=AsyncMock()
    )


def deps(tmp_path, ctx):
    docker = AsyncMock()
    docker.start_container = AsyncMock(return_value="ctr-1")
    docker.connect_network = AsyncMock()
    docker.exec_command = AsyncMock(return_value=(0, "ok"))
    dns = AsyncMock()
    pki = AsyncMock()
    pki.issue_cert = AsyncMock(return_value=("CERT", "KEY"))
    pki.extract_cert_expiry = MagicMock(return_value=datetime.now(UTC) + timedelta(days=1))
    oidc = AsyncMock()
    oidc.create_client = AsyncMock()
    handler = AppHandler(
        docker,
        dns,
        pki,
        oidc,
        ctx.runtime_state,
        ctx,
        catalog=AppCatalog(ctx.spec.org_apps.catalog),
    )
    handler._db = mock_db()
    return handler, docker, dns, pki, oidc


@pytest.mark.asyncio
async def test_deploy_app_uses_catalog_and_persists_status(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    handler, docker, dns, _pki, oidc = deps(tmp_path, ctx)

    result = await handler.deploy_app("acme", "demo", "demo")

    assert result["status"] == "deployed"
    docker.start_container.assert_awaited_once()
    assert docker.start_container.call_args.kwargs["image"] == "demo:1"
    assert docker.start_container.call_args.kwargs["ports"] == [8080]
    dns.add_zone_record.assert_awaited_once()
    oidc.create_client.assert_awaited_once_with(
        realm="inworld",
        client_id="acme-demo",
        name="acme demo",
        redirect_uris=["https://demo.acme.internal/*"],
        public=True,
    )
    upserts = handler._db.table.return_value.upsert.call_args_list
    assert [c.args[0]["status"] for c in upserts] == ["deploying", "deployed"]


@pytest.mark.asyncio
async def test_certificate_is_mounted_before_container_start(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    handler, docker, *_ = deps(tmp_path, ctx)

    await handler.deploy_app("acme", "demo", "demo")

    volumes = docker.start_container.call_args.kwargs["volumes"]
    cert_dir = tmp_path / "netengines_acme_demo"
    assert (cert_dir / "tls.crt").read_text() == "CERT"
    assert volumes[str(cert_dir)] == {"bind": "/certs", "mode": "ro"}
    assert not hasattr(docker, "copy_to_container") or docker.copy_to_container.await_count == 0


@pytest.mark.asyncio
async def test_dns_failure_persists_failure_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    handler, _docker, dns, *_ = deps(tmp_path, ctx)
    dns.add_zone_record.side_effect = RuntimeError("dns down")

    with pytest.raises(RuntimeError, match="dns down"):
        await handler.deploy_app("acme", "demo", "demo")

    failed = handler._db.table.return_value.upsert.call_args_list[-1].args[0]
    assert failed["status"] == "failed"
    assert failed["failure_detail"] == "dns down"


@pytest.mark.asyncio
async def test_oidc_client_creation_failure_persists_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    handler, _docker, _dns, _pki, oidc = deps(tmp_path, ctx)
    oidc.create_client.side_effect = RuntimeError("oidc failed")

    with pytest.raises(RuntimeError, match="oidc failed"):
        await handler.deploy_app("acme", "demo", "demo")

    failed = handler._db.table.return_value.upsert.call_args_list[-1].args[0]
    assert failed["status"] == "failed"
    assert failed["failure_detail"] == "oidc failed"


@pytest.mark.asyncio
async def test_idempotent_redeploy_uses_stable_name_and_domain(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    handler, docker, *_ = deps(tmp_path, ctx)

    first = await handler.deploy_app("acme", "demo", "demo")
    second = await handler.deploy_app("acme", "demo", "demo")

    assert first["domain"] == second["domain"] == "demo.acme.internal"
    names = [call.kwargs["name"] for call in docker.start_container.call_args_list]
    assert names == ["netengines_acme_demo", "netengines_acme_demo"]


@pytest.mark.asyncio
async def test_org_apps_phase_uses_runtime_identity_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    ctx.docker_client = AsyncMock()
    ctx.docker_client.start_container = AsyncMock(return_value="ctr-1")
    ctx.docker_client.connect_network = AsyncMock()

    with (
        patch("netengine.handlers.app_handler.OIDCHandler") as oidc_cls,
        patch("netengine.handlers.app_handler.PKIHandler") as pki_cls,
        patch("netengine.handlers.app_handler.DNSHandler") as dns_cls,
        patch(
            "netengine.handlers.app_handler.AppHandler._get_db", AsyncMock(return_value=mock_db())
        ),
    ):
        pki = AsyncMock()
        pki.issue_cert = AsyncMock(return_value=("CERT", "KEY"))
        pki.extract_cert_expiry = MagicMock(return_value=datetime.now(UTC) + timedelta(days=1))
        pki_cls.return_value = pki
        dns_cls.return_value = AsyncMock()
        oidc_cls.return_value = AsyncMock()

        await OrgAppsPhaseHandler().execute(ctx)

    oidc_cls.assert_called_once_with(
        keycloak_url="https://auth.acme.internal",
        admin_username="kc-admin",
        admin_password="from-state",
    )
    assert ctx.runtime_state.org_apps_output["deployments"][0]["status"] == "deployed"


@pytest.mark.asyncio
async def test_lifecycle_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("NETENGINE_APP_CERT_DIR", str(tmp_path))
    ctx = context(tmp_path)
    handler, docker, *_ = deps(tmp_path, ctx)

    assert await handler.healthcheck_app("ctr-1", "/health") is True
    await handler.teardown_app({"org": "acme", "app": "demo", "container_id": "ctr-1"})

    docker.exec_command.assert_awaited_once()
    docker.stop_container.assert_awaited_once_with("ctr-1")
    assert handler._db.table.return_value.upsert.call_args.args[0]["status"] == "torn_down"
