"""Tests for typed event factories."""

from netengine.events import factory as event_factory


def test_world_registry_factories_set_known_types_and_payloads() -> None:
    admitted = event_factory.org_admitted(
        org_name="acme", capabilities=["dns"], and_profile="residential"
    )
    assert admitted.event_type == "org.admitted"
    assert admitted.emitted_by == "world_registry_handler"
    assert admitted.payload == {
        "org_name": "acme",
        "capabilities": ["dns"],
        "and_profile": "residential",
    }

    removed = event_factory.org_removed(org_name="acme")
    assert removed.event_type == "org.removed"
    assert removed.payload == {"org_name": "acme"}


def test_domain_and_drift_factories_set_known_types() -> None:
    domain = event_factory.domain_registered(
        domain="acme.internal", org_name="acme", ns_records=["ns1.internal"]
    )
    assert domain.event_type == "domain.registered"
    assert domain.payload == {"domain": "acme.internal", "org": "acme", "ns": ["ns1.internal"]}

    drift = event_factory.drift_detected(phase=3, handler="DNSHandler", detected_at="now")
    assert drift.event_type == "drift.detected"
    assert drift.payload == {"phase": 3, "handler": "DNSHandler", "detected_at": "now"}


def test_extension_factory_marks_explicit_unknown_payloads() -> None:
    event = event_factory.extension_event(
        event_type="vendor.custom", emitted_by="extension", data={"key": "value"}
    )
    assert event.payload == {"__extension_event__": True, "data": {"key": "value"}}
