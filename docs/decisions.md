# Architecture Decisions (Locked at M0)

This document captures three critical architecture decisions that must be locked before implementation proceeds. Changing these later requires refactoring across M1-M8 handlers.

---

## Decision 1: Supabase Deployment Model — Cloud

**Status:** DECIDED  
**Date:** M0  
**Locked for:** M1-M8 handler development

### Decision

Use **cloud-hosted Supabase** (not self-hosted) for the MVP ephemeral lifecycle.

### Rationale

- **MVP Velocity:** Cloud deployment eliminates DevOps setup burden. Self-hosted Supabase adds operational complexity not justified for MVP validation.
- **Acceptable Scope Limitation:** MVP targets the dev sandbox archetype. Governance data leaving the world is a tension point only for the alternative-internet-substrate archetype (future, persistent mode scope).
- **Simpler Operations:** Cloud eliminates container orchestration, backup strategy, HA topology decisions for M0-M7.

### Implications

- All Supabase client connections use cloud endpoints
- `.env` file configures `SUPABASE_URL` and `SUPABASE_ANON_KEY` from cloud dashboard
- CI fixtures use cloud endpoints (or local test containers for unit tests)
- Schema migrations run against cloud Postgres (M3+)

### Trade-off

- **Pro:** Faster MVP, simpler ops, focus on domain logic
- **Con:** Governance data external; unsuitable for authority-autonomous posture (persistent mode will use self-hosted)

### Future Path

Persistent mode scope includes:
- Self-hosted Supabase deployment
- Connection config abstraction for environment-specific endpoints
- Operational runbook for self-hosted Supabase

---

## Decision 2: pgmq Event Envelope Schema — Locked at M0

**Status:** DECIDED  
**Date:** M0  
**Used by:** M4+ handlers (all inter-handler communication)

### Decision

All inter-handler events (M4+) must use this envelope schema:

```python
@dataclass
class EventEnvelope:
    event_id: str                  # UUID v4, unique per event
    correlation_id: str            # Trace ID (same for all events in chain)
    parent_event_id: Optional[str] # Direct parent in causality chain
    event_type: str                # e.g., "dns.zone_update_required"
    emitted_by: str                # Handler name (e.g., "dns_handler")
    emitted_at: datetime           # ISO 8601 timestamp
    payload: dict[str, Any]        # Handler-specific data
    retry_count: int = 0           # DLQ retry counter
```

### Rationale

- **Causality Tracing:** `correlation_id` + `parent_event_id` enables operator visibility into event chains (M4+)
- **Event Graph:** Minimum fields support querying full event history for a world operation
- **Retry Semantics:** `retry_count` is essential for DLQ (dead-letter queue) logic; post-MVP metrics on stuck events
- **Deferred Retrofitting:** If this schema is not locked now, adding correlation IDs across all M4-M8 handlers post-facto is extremely painful

### Implications

- **M4+**: All handlers that emit events use this envelope when publishing to pgmq
- **Operator API:** Endpoint `GET /api/v1/events/{correlation_id}` returns full causal chain for a world operation
- **DLQ Handling:** After N retries, messages move to DLQ. Operator must explicitly replay from `GET /api/v1/queues`

### Message Storage

Messages are stored in pgmq as JSON:

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "parent_event_id": null,
  "event_type": "domain_registry.domain_registered",
  "emitted_by": "domain_registry_handler",
  "emitted_at": "2026-06-21T12:34:56Z",
  "payload": {
    "domain_name": "acme.internal",
    "org": "acme-corp",
    "ns_records": [...]
  },
  "retry_count": 0
}
```

### Example Event Chain

```
Root event: org_admitted (correlation_id: "abc-123")
├─ Child: oidc_handler.realm_created (parent: abc-123)
├─ Child: and_handler.bridge_created (parent: abc-123)
│  └─ Grandchild: gateway_handler.rules_applied (parent: bridge-event-id)
│  └─ Grandchild: dns_handler.zone_registered (parent: bridge-event-id)
```

All events share `correlation_id: "abc-123"`. Operator queries `/api/v1/events/abc-123` to see full chain.

### Future Path

- M8+: Event graph stored in Supabase for durability and querying
- Persistent mode: Event retention SLO, metrics on DLQ age
- Cross-world federation: Correlation IDs bridge events across world boundaries

---

## Decision 3: BaseGatewayHandler Interface — Locked at M0

**Status:** DECIDED  
**Date:** M0  
**Used by:** M7+ handlers (AND profile → network policy)

### Decision

All gateway implementations must conform to this interface:

```python
class BaseGatewayHandler(ABC):
    async def generate_rules(self, context: PhaseContext) -> list[Rule]:
        """Generate rules from AND profiles or service definitions."""
        pass

    async def apply_rules(self, context: PhaseContext, rules: list[Rule]) -> None:
        """Apply rules to gateway atomically."""
        pass

    async def remove_rules(self, context: PhaseContext, rule_ids: list[str]) -> None:
        """Remove specific rules by ID."""
        pass

    async def reload(self, context: PhaseContext) -> None:
        """Reload full gateway config (post-restart)."""
        pass
```

### Rationale

- **Gateway Abstraction:** Interface decouples AND handler (M7) from gateway implementation
- **Multi-Gateway Support:** Enables swapping Alpine+nftables (M0) → VyOS (BGP scope, future) without refactoring AND handler or Phase 7
- **Profile Extensibility:** New AND profiles (e.g., cyber-range attacker profile) plug in via profile definitions, not handler rewrites
- **Implementation Flexibility:** Each implementation encapsulates technology-specific details (nftables syntax, VyOS CLI config, etc.)

### Implications

- **M0:** Only `nftables_gateway_handler` implemented (Alpine Linux + nftables on gateway container)
- **M7:** AND handler calls `gateway_handler.generate_rules()` for each AND, then `apply_rules()` to commit atomically
- **Future BGP Scope:** Swap handler to `vyos_gateway_handler` without touching AND handler call sites

### Interface Contract

**Rule Generation:**
```python
rules = await gateway_handler.generate_rules(context)
# Returns: [
#   Rule(rule_id="residential-nat", priority=100, content={nftables config}),
#   Rule(rule_id="business-accept", priority=110, content={nftables config}),
#   ...
# ]
```

**Rule Application:**
```python
await gateway_handler.apply_rules(context, rules)
# Applies all rules atomically. If any rule fails, gateway state is undefined.
# Handler must make the entire operation atomic (e.g., nft -f applies all or none).
```

**Rule Removal:**
```python
await gateway_handler.remove_rules(context, ["residential-nat", "business-accept"])
# Remove specific rules. AND deprovision calls this when removing an AND.
```

**Full Reload:**
```python
await gateway_handler.reload(context)
# Full gateway restart / config reload. Called after gateway container restart,
# or when switching gateway implementations.
```

### M0 Implementation Strategy

**nftables_gateway_handler:**
- Generate nftables ruleset as text from AND profile definitions
- Validate ruleset with `nft --check` before applying
- Apply via `docker exec netengines_gateway nft -f /etc/nftables/rules.nft`
- Atomic reload: `nft -f` applies entire ruleset in one operation

Example profile → ruleset translation:
```python
# Input: AND profile (residential, nat=true, inbound=blocked)
# Output: nftables ruleset with:
#   - postrouting NAT masquerade rule
#   - prerouting drop for unsolicited inbound
#   - no reverse DNS
```

### Test Strategy

**Unit tests (not integration):**
- Profile definition → nftables ruleset translation
- Parse generated ruleset with `nft --check`
- Assert semantic intent: residential has masquerade, business has inbound accept, etc.
- Never simulate gateway behavior — always test the translation layer

**Why not a gateway simulator?**
- Correct test lives in the profile → ruleset translation logic, not in a parallel gateway simulator
- Simulator adds a second environment to maintain (which can drift from real nftables)
- Real test: does the profile definition produce correct nftables syntax?

### Future Path

- **VyOS implementation:** `vyos_gateway_handler` implements same interface, generates VyOS CLI config
- **Custom rules:** If AND profile needs custom rule logic, extend BaseGatewayHandler without changing call sites
- **Cross-world routing:** Add cross-world peer routes to ruleset in federated mode (M9+)

---

## Summary Table

| Decision | Status | Impact | Locked For |
|----------|--------|--------|-----------|
| Supabase: Cloud | ✓ Decided | Connection config, CI setup, ops posture | M1-M8 handler dev |
| pgmq Event Schema | ✓ Decided | All inter-handler events (M4+), operator API | M4+ event wiring |
| BaseGatewayHandler | ✓ Decided | M7+ AND policy, M9+ cross-world routing | M7+ handler impl |

---

## Amendment Process

To amend any locked decision:

1. Document the rationale for change (why was the original decision insufficient?)
2. Identify all handlers / modules affected by the change
3. Plan refactoring scope
4. Obtain consensus from team leads
5. Update this document with amendment date, rationale, and scope

Changes to locked decisions create large refactoring waves. Prefer extending / composing solutions first, then amending only if composition is clearly worse.
