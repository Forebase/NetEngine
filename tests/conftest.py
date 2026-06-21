"""Pytest configuration and shared fixtures."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from netengine.handlers.context import PhaseContext, RuntimeState
from netengine.logging.logger import get_logger
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


@pytest.fixture
def runtime_state() -> RuntimeState:
    """Fresh runtime state for each test."""
    return RuntimeState()


# ─────────────────────────────────────────────
# Phase Context Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def phase_context(
    minimal_spec: NetEngineSpec,
    runtime_state: RuntimeState,
    logger: logging.Logger,
) -> PhaseContext:
    """Phase context ready for handler testing."""
    return PhaseContext(
        spec=minimal_spec,
        runtime_state=runtime_state,
        logger=logger,
    )


@pytest.fixture
def phase_context_single_org(
    single_org_spec: NetEngineSpec,
    runtime_state: RuntimeState,
    logger: logging.Logger,
) -> PhaseContext:
    """Phase context with single-org spec."""
    return PhaseContext(
        spec=single_org_spec,
        runtime_state=runtime_state,
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
