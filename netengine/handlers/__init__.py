"""Phase handlers and handler interfaces."""

from netengine.core.state import RuntimeState
from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.context import PhaseContext
from netengine.handlers.dns import DNSHandler
from netengine.handlers.substrate import SubstrateHandler

__all__ = [
    "BasePhaseHandler",
    "PhaseContext",
    "RuntimeState",
    "SubstrateHandler",
    "DNSHandler",
]
