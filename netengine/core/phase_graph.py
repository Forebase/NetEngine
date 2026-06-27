"""Phase dependency graph for NetEngine bootstrap orchestration.

Centralises the authoritative list of phase handlers and their prerequisites
so Orchestrator, DriftDetectionController, and any future tooling can import
these rather than duplicating or scattering them.
"""

from typing import Type

from netengine.handlers._base import BasePhaseHandler
from netengine.handlers.app_handler import OrgAppsPhaseHandler
from netengine.handlers.dns import DNSHandler
from netengine.handlers.phase_pki import PKIPhaseHandler
from netengine.handlers.substrate import SubstrateHandler
from netengine.phases.phase_ands import ANDsPhaseHandler
from netengine.phases.phase_inworld_identity import InWorldIdentityPhaseHandler
from netengine.phases.phase_platform_identity import PlatformIdentityPhaseHandler
from netengine.phases.phase_registries import RegistriesPhaseHandler
from netengine.phases.phase_services import ServicesPhaseHandler

# Ordered list of (phase_number, handler_class) pairs.
# DNS intentionally omits Phase 2: DNSHandler performs both Phase 1 (root/platform
# zones) and Phase 2 (TLD setup) in a single combined operation, then marks both
# complete in _mark_phase_complete.
PHASE_HANDLERS: list[tuple[int, Type[BasePhaseHandler]]] = [
    (0, SubstrateHandler),
    (1, DNSHandler),
    (3, PKIPhaseHandler),
    (4, PlatformIdentityPhaseHandler),
    (5, RegistriesPhaseHandler),
    (6, InWorldIdentityPhaseHandler),
    (7, ANDsPhaseHandler),
    (8, ServicesPhaseHandler),
    (9, OrgAppsPhaseHandler),
]

# RuntimeState field(s) that must be truthy before a phase may run.
# Any phase absent from this dict has no prerequisites beyond prior completion.
PHASE_PREREQUISITES: dict[int, list[str]] = {
    3: ["dns_output"],
    4: ["pki_bootstrapped"],
    5: ["identity_platform_output"],
    6: ["world_registry_output", "domain_registry_output"],
    7: ["identity_inworld_output"],
    8: ["ands_output"],
}
