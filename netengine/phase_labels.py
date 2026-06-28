"""Shared phase labels for CLI and API status surfaces."""

from __future__ import annotations

PHASE_LABELS = {
    "0": "Substrate",
    "1": "DNS root + platform zones",
    "2": "DNS TLD hierarchy",
    "3": "PKI + ACME",
    "4": "Platform identity",
    "5": "Registries",
    "6": "In-world identity",
    "7": "ANDs",
    "8": "Services",
    "9": "Org applications",
}
