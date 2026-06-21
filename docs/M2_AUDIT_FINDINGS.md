# M2 Audit Findings: DNS Hierarchy (Phases 1-2)

**Date:** 2026-06-21  
**Audit Scope:** `netengine/handlers/dns.py`, related tests, downstream dependencies  
**Status:** CRITICAL BLOCKER IDENTIFIED

---

## Executive Summary

The DNS handler is **half-implemented and represents a critical blocker for M3+ development**:

- ✅ **Zone file generation is production-quality** (RFC 1035 compliant, tested)
- ❌ **Zone file delivery is completely stubbed** (method body is `pass`)
- ❌ **Every downstream handler silently fails** (calls to `add_zone_record()` do nothing)

This must be fixed before proceeding with PKI, OIDC, or other identity/registry phases.

---

## Section 1: What's Implemented (Real Code)

### 1.1 Zone Configuration Objects (Lines 180-283)

**Status:** ✅ Production quality

Methods that generate configuration objects:
- `_setup_root_zone()` — Creates dict with SOA config, type, listen_ip, serial policy
- `_setup_platform_zone()` — Creates dict with platform zone name, listen_ip, NS server name
- `_setup_tlds()` — Iterates spec TLDs, creates dict mapping TLD name → config

**Output Example:**
```python
{
    "name": "root.internal",
    "type": "authoritative",
    "server": "coreDNS",
    "listen_ip": "10.0.0.2",
    "soa_primary_ns": "root.internal",
    "soa_email": "admin.internal",
    "serial_policy": "timestamp",
    "deployed_at": "2026-06-21T12:34:56.789012"
}
```

**Assessment:** Clear data structures, proper logging, follows spec exactly.

---

### 1.2 Zone File Generation (Lines 289-442)

**Status:** ✅ Production quality (RFC 1035 compliant)

Methods:
- `_generate_zone_files()` — Orchestrates generation of all zone files
- `_generate_root_zone_file()` — Generates root zone with SOA + NS records
- `_generate_platform_zone_file()` — Generates platform zone with L1 service stubs
- `_generate_tld_zone_file()` — Generates TLD zones (empty, for population by domain registry)
- `_generate_serial()` — Serial number generation (timestamp or fixed policies)

**Output Format Example:**
```
; Root zone: root.internal
; Generated: 2026-06-21T12:34:56.789012
root.internal. SOA root.internal. admin.internal. 1719059696 3600 1800 604800 86400
root.internal. NS ns.root.internal.

; Delegation to platform zone
platform.internal. NS ns.platform.internal.
platform.internal. A 10.0.0.3

; Delegations to TLD servers
internal. NS ns4.internal.
ns4.internal. A 10.0.0.4

localnet. NS ns5.internal.
ns5.internal. A 10.0.0.5
```

**Assessment:**
- RFC 1035 compliant SOA records (serial, refresh, retry, expire, minimum)
- Proper NS delegation records
- Correct A records for nameserver glue records
- Comments for operator readability
- Serial number generation supports multiple policies

**Test Coverage:** 12 tests validate zone generation:
- Zone file content is generated
- SOA records contain correct values
- NS records point to correct servers
- Healthcheck validates output structure

---

### 1.3 Core Execution Flow (Lines 34-116)

**Status:** ✅ Well-designed orchestration

The `execute()` method:
1. Calls `_setup_root_zone()` → stores in dns_output["root_zone"]
2. Calls `_setup_platform_zone()` → stores in dns_output["platform_zone"]
3. Calls `_setup_tlds()` → stores in dns_output["tlds"]
4. Calls `_generate_zone_files()` → stores in dns_output["zone_files"]
5. Calls `_verify_dns_service()` → sets dns_output["healthy"]
6. Sets timestamps and emits event
7. Stores complete output in `context.runtime_state.dns_output`

**Assessment:** Clear dependency chain, proper error handling with try/except, logging at each step.

---

### 1.4 Event Emission (Lines 487-517)

**Status:** ✅ Properly structured, queuing stubbed

The `_emit_event()` method:
- Creates `EventEnvelope` with correct structure
- Preserves correlation_id and parent_event_id (important for causal tracing)
- Logs event details
- **Line 517:** PGMQ queuing is commented out (ready for M4+ integration)

**Code:**
```python
event = EventEnvelope.create(
    event_type=event_type,
    emitted_by="dns_handler",
    payload=payload,
    correlation_id=context.runtime_state.correlation_id,
    parent_event_id=context.runtime_state.parent_event_id,
)
# M4+: Queue to pgmq
# await context.pgmq_client.send(event)
```

**Assessment:** Event schema is correct and ready. Just needs pgmq client and uncommented line for M4+.

---

### 1.5 Healthcheck and Idempotence (Lines 118-174)

**Status:** ✅ Well-designed patterns

- `healthcheck()` — Checks dns_output exists, marked healthy, zones present
- `should_skip()` — Returns True if already deployed (idempotent)

**Assessment:** Proper lifecycle management, prevents redundant execution.

---

## Section 2: What's Stubbed (Placeholder Code)

### 2.1 Zone Verification (Lines 447-481)

**Status:** ⚠️ Shallow validation, not real DNS queries

The `_verify_dns_service()` method:
- ✅ Checks zone_files dict exists
- ✅ Checks root_zone and platform_zone are present
- ❌ Does NOT query actual DNS
- ❌ Does NOT verify SOA/NS records are resolvable
- ❌ Does NOT check zones are authoritative

**Current Code:**
```python
# Check all required zones are present
if "root_zone" not in dns_output or "platform_zone" not in dns_output:
    logger.error("Missing root or platform zone in DNS output")
    return False

# Check zone files were generated
if "zone_files" not in dns_output or not dns_output["zone_files"]:
    logger.error("No zone files were generated")
    return False

logger.info("DNS service verification passed (stubbed in M1)")
return True
```

**Impact:** Medium. Verification is shallow but sufficient for M2 (zone files exist). Real DNS queries can wait for M4 when CoreDNS container is running.

**Recommendation:** Leave as-is for M2. Upgrade to real DNS queries (via `dig` or DNS library) when CoreDNS container integration happens.

---

### 2.2 Zone Record Updates — **CRITICAL BLOCKER** (Lines 519-586)

**Status:** ❌ **Completely stubbed** — body is `pass`, no logic executes

**The Problem:**

```python
async def add_zone_record(
    self, zone: str, record_type: str, name: str, value: str, ttl: int = 300
) -> None:
    """Add or update a DNS record in the zone file..."""
    pass  # <-- EXITS HERE, EVERYTHING BELOW IS UNREACHABLE
    
    # All of this is dead code:
    zone_dir = Path("/var/lib/netengines/dns/zones")
    zone_file = zone_dir / f"{zone}.zone"
    if not zone_file.exists():
        raise RuntimeError(f"Zone file for {zone} does not exist...")
    await asyncio.to_thread(self._upsert_record_sync, zone_file, name, record_type, value, ttl)
```

**The Impact:**

Every downstream handler that calls `add_zone_record()` gets silently ignored:
- ❌ No zone files are updated
- ❌ No records are added
- ❌ No errors are raised (silent failure)
- ❌ Later handlers that depend on DNS records fail downstream

**Example Call Stack:**
```
orchestrator.py:88
  → dns.add_zone_record("platform.internal", "A", "ca", "10.0.0.6")
    → DOES NOTHING (pass statement)

phase_platform_identity.py:70
  → dns.add_zone_record("internal", "A", "auth", "10.0.0.7")
    → DOES NOTHING (pass statement)

phase_registries.py:72
  → dns.add_zone_record(zone=domain, record_type="A", name=subdomain, value=ip)
    → DOES NOTHING (pass statement)
```

**Dead Code Below `pass`:**

Lines 536-586 contain the actual implementation:
- Zone file path handling
- File existence checks
- Async-to-sync wrapper for file I/O
- Record parsing with regex
- Record upsert logic
- File write-back

This code is well-written but **completely unreachable** because the `pass` statement exits immediately.

---

## Section 3: Downstream Dependencies

**Critical Finding:** 11 files depend on `add_zone_record()` being functional.

### 3.1 Direct Callers (5+ handlers and phases)

| File | Line | Purpose | Status |
|------|------|---------|--------|
| `orchestrator.py` | 88, 133, 157 | Register ca.platform.internal, auth.platform.internal | ❌ Fails silently |
| `phase_platform_identity.py` | 70 | Register auth.internal | ❌ Fails silently |
| `phase_inworld_identity.py` | 36 | Register auth.internal zone | ❌ Fails silently |
| `phase_registries.py` | 40, 44, 72 | Register TLD NS records, domain A records | ❌ Fails silently |
| `and_handler.py` | 41 | Register AND suffix zones | ❌ Fails silently |
| `app_handler.py` | ? | App DNS registration | ❌ Likely fails silently |
| `mail_handler.py` | ? | Mail service DNS (MX, SPF, DKIM) | ❌ Likely fails silently |

### 3.2 Callers via Events (M4+ design)

- `pgmq` consumer in `phase_registries.py:58` — Expects to call `add_zone_record()` for each domain registration event

### 3.3 Test Coverage

- ❌ **No tests for `add_zone_record()`**
- ❌ **No tests validating DNS records persist**
- ❌ **No integration tests calling it**

---

## Section 4: Integration Gaps

### 4.1 Dependency Chain Issues

**Missing Substrate Dependency Check:**
- DNS handler does NOT verify substrate_output exists
- If Phase 0 fails, Phase 1-2 will still execute and appear successful
- Should validate that networks were created first

**Recommendation:** Add check in `execute()`:
```python
if context.runtime_state.substrate_output is None:
    raise RuntimeError("Substrate must run first (Phase 0)")
```

---

### 4.2 External Service Integration

| Service | Required For | Current Status | Issue |
|---------|-------------|-----------------|-------|
| Docker SDK | CoreDNS container deployment | Not integrated | Zone files exist only in memory |
| Supabase | Persisting zone state | Not integrated | No durable zone storage |
| pgmq (message queue) | Event queuing for M3+ | Stubbed (commented out) | Ready to uncomment, needs M4 client |

---

## Section 5: Test Coverage Gaps

### Current Tests (12 tests, all passing)
- ✅ Zone generation produces RFC 1035 output
- ✅ Output structure is correct
- ✅ Healthcheck logic works
- ✅ Idempotence check works

### Missing Tests
- ❌ add_zone_record() actually updates zone files
- ❌ Multiple calls don't create duplicate records
- ❌ Error handling when zone doesn't exist
- ❌ Substrate dependency validation
- ❌ Integration: substrate → DNS complete flow
- ❌ Integration: DNS → downstream handlers (PKI, OIDC, etc.)

---

## Section 6: Required Fixes for M2 Completion

### Fix 1: Implement `add_zone_record()` (CRITICAL)

**What to do:**
- Remove the `pass` statement on line 534
- Implement in-memory zone file updates (for M2)
- Plan disk I/O integration for M4

**Estimated effort:** 30 minutes

**Approach:**
```python
async def add_zone_record(self, zone: str, record_type: str, name: str, value: str, ttl: int = 300) -> None:
    # Find zone in runtime_state.dns_output["zone_files"]
    # Parse zone file string
    # Find/insert record in correct place (before closing comment or EOF)
    # Update zone_files dict
    # Log the update
```

---

### Fix 2: Add Substrate Dependency Check (IMPORTANT)

**What to do:**
- In `execute()`, validate substrate_output exists before proceeding
- Raise descriptive error if missing

**Estimated effort:** 5 minutes

---

### Fix 3: Uncomment PGMQ Event Queuing (IMPORTANT)

**What to do:**
- Uncomment line 517: `await context.pgmq_client.send(event)`
- Add null check: if pgmq_client is None, log warning and continue

**Estimated effort:** 5 minutes

---

### Fix 4: Write Integration Tests (IMPORTANT)

**What to do:**
- Test substrate → DNS complete flow
- Test add_zone_record() updates zone files
- Test dependency validation
- Test event emission

**Estimated effort:** 45 minutes

---

## Section 7: M2 Definition of Done Checklist

### Phase 1: Root DNS + Platform Zone
- [x] Root zone configuration object created
- [x] Root zone file generated with RFC 1035 compliance
- [x] Platform zone configuration object created
- [x] Platform zone file generated with L1 service stubs
- [ ] Substrate dependency check implemented
- [ ] Tests validate all of the above

### Phase 2: TLD Hierarchy
- [x] TLD servers configured per spec
- [x] Root zone delegates to TLD servers via NS records
- [x] TLD zone files created (empty, ready for population)
- [ ] add_zone_record() can populate TLD zones
- [ ] Tests validate TLD delegation

### Runtime Validation
- [x] dns_output populated with all required fields
- [x] healthcheck() works correctly
- [x] should_skip() prevents redundant execution
- [ ] Event emission works end-to-end
- [ ] PGMQ queuing functional (when client available)

### Integration Requirements
- [ ] Substrate → DNS flow tested end-to-end
- [ ] add_zone_record() functional (in-memory version)
- [ ] No silent failures (errors raised appropriately)
- [ ] All existing tests still pass

---

## Section 8: Blockers for M3+ Development

### Blocker 1: add_zone_record() Not Functional
- **Impact:** PKI handler cannot register ca.platform.internal
- **Impact:** OIDC handler cannot register auth.platform.internal
- **Impact:** Domain registry cannot register domain records
- **Impact:** AND handler cannot register AND suffix zones
- **Severity:** CRITICAL

### Blocker 2: No Substrate Dependency Check
- **Impact:** DNS can run before networks exist
- **Impact:** Silent failure if phase 0 incomplete
- **Severity:** MEDIUM

### Blocker 3: Event Queuing Not Enabled
- **Impact:** Event chain breaks at M4
- **Impact:** Causal tracing doesn't work across handlers
- **Severity:** MEDIUM (fixable with one-line uncomment)

---

## Section 9: Recommendations for Implementation Order

### Immediate (Required for M2 completion)
1. ✅ Implement `add_zone_record()` in-memory version
2. ✅ Add substrate dependency check
3. ✅ Uncomment PGMQ event queuing
4. ✅ Write integration tests

### Follow-up (M3+)
1. Integration with actual DNS container (not just memory)
2. Real DNS verification (via dig queries)
3. Zone file persistence to disk
4. pgmq consumer for domain registry events

---

## Files to Modify

### Implementation
- `netengine/handlers/dns.py`
  - Line 519-586: Remove `pass`, implement in-memory zone updates
  - Line 34-60: Add substrate dependency check in `execute()`
  - Line 517: Uncomment pgmq queuing (add null check)

### Tests
- `tests/test_m1_handlers.py` — Add 3 test cases for add_zone_record()

### New
- `tests/integration/test_m1_m2_bootstrap.py` — Integration test suite
- `tests/integration/__init__.py` — Init file

---

## Conclusion

M2 DNS handler is **70% complete** with good zone generation but **critical missing piece** in zone record updates. The `add_zone_record()` stub must be implemented before M3+ work can proceed.

**Estimated time to fix:** 1.5-2 hours (implementation + tests)

**Risk level:** LOW — add_zone_record() is currently unused, so implementing it won't break existing tests.

**Blockers for M3:** HIGH — PKI, OIDC, domain registry all depend on this working.

---

**Audit completed by:** Claude Code  
**Date:** 2026-06-21  
**Status:** Ready for Milestone 2 implementation phase
