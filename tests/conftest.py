"""Pytest configuration and shared fixtures."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from netengine.handlers.context import PhaseContext, RuntimeState
from netengine.logging import get_logger
from netengine.spec.loader import load_spec
from netengine.spec.models import NetEngineSpec

# ─────────────────────────────────────────────
# Logger Fixture
# ─────────────────────────────────────────────


@pytest.fixture
def logger() -> logging.Logger:
    """Logger instance for tests."""
    return get_logger("test")


# ─────────────────────────────────────────────
# Spec Fixtures (load example specs)
# ─────────────────────────────────────────────


def _get_examples_dir() -> Path:
    """Get path to examples directory."""
    return Path(__file__).parent.parent / "examples"


@pytest.fixture
def minimal_spec() -> NetEngineSpec:
    """Minimal example spec."""
    examples_dir = _get_examples_dir()
    return load_spec(examples_dir / "minimal.yaml")


@pytest.fixture
def single_org_spec() -> NetEngineSpec:
    """Single-org example spec."""
    examples_dir = _get_examples_dir()
    return load_spec(examples_dir / "single-org.yaml")


@pytest.fixture
def dev_sandbox_spec() -> NetEngineSpec:
    """Dev sandbox example spec."""
    examples_dir = _get_examples_dir()
    return load_spec(examples_dir / "dev-sandbox.yaml")


# ─────────────────────────────────────────────
# Runtime State Fixtures
# ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_runtime_state_file(tmp_path, monkeypatch):
    """Keep tests from reading or writing the repository-root runtime state file."""
    monkeypatch.setenv("NETENGINE_STATE_FILE", str(tmp_path / "netengine_state.json"))


@pytest.fixture
def runtime_state() -> RuntimeState:
    """Fresh runtime state for each test."""
    return RuntimeState()


@pytest.fixture
def runtime_state_with_substrate() -> RuntimeState:
    """Runtime state pre-populated with substrate output.

    Used for DNS and later phase tests that require Phase 0 to have run first.
    Substrate tests use the plain runtime_state fixture instead.
    """
    state = RuntimeState()
    # Mock substrate output so DNS handler dependency check passes
    state.substrate_output = {
        "orchestrator": "docker",
        "networks": {
            "platform": {"subnet": "172.20.0.0/16", "created": True},
            "core": {"subnet": "10.0.0.0/8", "created": True},
        },
        "gateway": {"platform_ip": "172.20.0.1", "core_ip": "10.0.0.1"},
        "ntp": {"enabled": True, "synced": True},
        "healthy": True,
    }
    return state


# ─────────────────────────────────────────────
# Phase Context Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def phase_context(
    minimal_spec: NetEngineSpec,
    runtime_state_with_substrate: RuntimeState,
    logger: logging.Logger,
) -> PhaseContext:
    """Phase context ready for DNS and later handler testing.

    Uses runtime_state_with_substrate to mock Phase 0 completion,
    so DNS/later phases can run without actually executing substrate.
    """
    return PhaseContext(
        spec=minimal_spec,
        runtime_state=runtime_state_with_substrate,
        logger=logger,
    )


@pytest.fixture
def phase_context_substrate(
    minimal_spec: NetEngineSpec,
    runtime_state: RuntimeState,
    logger: logging.Logger,
) -> PhaseContext:
    """Phase context for Substrate handler testing.

    Uses plain runtime_state (no substrate_output pre-populated)
    so substrate handler tests can verify Phase 0 execution properly.
    """
    return PhaseContext(
        spec=minimal_spec,
        runtime_state=runtime_state,
        logger=logger,
    )


@pytest.fixture
def phase_context_single_org(
    single_org_spec: NetEngineSpec,
    runtime_state_with_substrate: RuntimeState,
    logger: logging.Logger,
) -> PhaseContext:
    """Phase context with single-org spec."""
    return PhaseContext(
        spec=single_org_spec,
        runtime_state=runtime_state_with_substrate,
        logger=logger,
    )


# ─────────────────────────────────────────────
# Mock Service Clients
# ─────────────────────────────────────────────


@pytest.fixture
def mock_docker_client() -> AsyncMock:
    """Mock Docker client."""
    client = AsyncMock()
    client.containers = AsyncMock()
    client.containers.list = AsyncMock(return_value=[])
    client.containers.create = AsyncMock()
    client.networks = AsyncMock()
    client.networks.create = AsyncMock()
    client.networks.connect = AsyncMock()
    return client


@pytest.fixture
def mock_supabase_client() -> MagicMock:
    """Mock Supabase client."""
    client = MagicMock()
    client.table = MagicMock()
    return client


@pytest.fixture
def mock_pgmq_client() -> AsyncMock:
    """Mock pgmq client."""
    client = AsyncMock()
    client.send = AsyncMock()
    client.read = AsyncMock(return_value=[])
    return client


# ─────────────────────────────────────────────
# Phase Context with Mocked Clients
# ─────────────────────────────────────────────


@pytest.fixture
def phase_context_with_mocks(
    phase_context: PhaseContext,
    mock_docker_client: AsyncMock,
    mock_supabase_client: MagicMock,
    mock_pgmq_client: AsyncMock,
) -> PhaseContext:
    """Phase context with mock service clients."""
    context = phase_context
    context.docker_client = mock_docker_client
    context.supabase_client = mock_supabase_client
    context.pgmq_client = mock_pgmq_client
    return context
