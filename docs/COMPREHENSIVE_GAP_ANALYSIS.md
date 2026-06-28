# NetEngine: Comprehensive Gap Analysis
## Complete Feature Gap Inventory (2026-06-27)

---

## Executive Summary

NetEngine exhibits **15+ categories of gaps** across the codebase, ranging from unused spec fields and incomplete event infrastructure to missing API endpoints and test coverage. These fall into three severity tiers:

| Severity | Category | Count | Examples |
|----------|----------|-------|----------|
| 🔴 **HIGH** | Unused Spec Fields (feature declared but never used) | 14+ | DNSSEC, CRL, OCSP, intermediate CA, service mirrors, cross-world peering |
| 🟡 **MEDIUM** | Missing Event Infrastructure | 4 | Event queues undefined, consumers not registered |
| 🟡 **MEDIUM** | API Gaps | 4+ | No endpoint for AND profile updates, gateway config, service toggles |
| 🟡 **MEDIUM** | Phase Prerequisites | 1 | Phase 9 missing prerequisite declaration |
| 🟢 **LOW** | Test Coverage Gaps | 8+ | No tests for declared features |
| 🟢 **LOW** | State/Schema Debt | 7 | Deprecated fields still accumulated |

**Total Impact**: ~30+ features partially or completely unimplemented, but declared in spec. Users can enable them in YAML configs with zero effect.

---

## 1. 🔴 CRITICAL: UNUSED SPEC FIELDS (HIGH SEVERITY)

### 1.1 PKI Configuration Gaps
**File**: `netengine/spec/models.py:201-228` (PKIPhase class)

These fields are declared in the spec but **completely absent from handler logic**:

| Field | Type | Default | Purpose | Files | Status |
|-------|------|---------|---------|-------|--------|
| `intermediate_ca_enabled` | bool | False | Enable cert hierarchy | models.py:222 | ❌ Never read |
| `dnssec_enabled` | bool | True | DNSSEC support | models.py:223 | ❌ Never read |
| `dnssec_ksk_lifetime_days` | int | 365 | KSK rotation lifetime | models.py:224 | ❌ Never read |
| `dnssec_zsk_lifetime_days` | int | 30 | ZSK rotation lifetime | models.py:225 | ❌ Never read |
| `crl_enabled` | bool | False | Certificate revocation list | models.py:226 | ❌ Never read |
| `ocsp_enabled` | bool | False | Online cert status protocol | models.py:227 | ❌ Never read |

**Evidence:**
```bash
$ grep -r "dnssec_enabled\|crl_enabled\|ocsp_enabled\|intermediate_ca_enabled" netengine/handlers/
# Returns: (nothing)
$ grep -r "dnssec_enabled\|crl_enabled\|ocsp_enabled\|intermediate_ca_enabled" netengine/phases/
# Returns: (nothing)
```

**Handler Only Implements**: Root CA generation via `step ca init` (netengine/handlers/pki_handler.py:80-96)

**Impact**: Users can set `pki: {dnssec_enabled: true, crl_enabled: true, intermediate_ca_enabled: true}` in their spec, but these settings have **zero effect**. The handlers don't check them, don't warn about them, don't implement them.

**Root Cause**: step-ca (the underlying tool) supports these features, but NetEngine's Phase 3 handler never wires them up.

---

### 1.2 Gateway Portal Configuration Gaps
**File**: `netengine/spec/models.py:514-551` (GatewayPortal, RealInternetConfig, CrossWorldConfig classes)

**Unused Gateway Fields:**

| Field | Type | Default | Purpose | Files | Status |
|-------|------|---------|---------|-------|--------|
| `real_internet.mode` | GatewayRealInternetMode | ISOLATED | Route to real internet | models.py:524 | ❌ Never checked |
| `real_internet.service_mirrors` | list[ServiceMirror] | [] | Mirror real services | models.py:525 | ❌ Never iterated |
| `real_internet.upstream_resolver_enabled` | bool | False | Use real DNS | models.py:526 | ❌ Never read |
| `real_internet.upstream_resolver_ip` | str | None | Real resolver IP | models.py:527 | ❌ Never read |
| `cross_world.mode` | GatewayCrossWorldMode | NONE | Federation mode | models.py:542 | ❌ Never checked |
| `cross_world.peers` | list[CrossWorldPeer] | [] | Peer worlds | models.py:543 | ❌ Never iterated |
| `cross_world.peers[].trust_anchor_cert` | str | None | PKI trust | models.py:536 | ❌ Never validated |

**Evidence**: 
```bash
$ grep -r "real_internet\|service_mirrors\|upstream_resolver\|cross_world" netengine/handlers/ netengine/phases/
# Returns: (nothing)
$ grep -r "GatewayRealInternetMode\|GatewayCrossWorldMode" netengine/
# Returns: only in spec/models.py (never instantiated or checked)
```

**Impact**: Users can write:
```yaml
gateway_portal:
  real_internet:
    mode: BRIDGED
    upstream_resolver_enabled: true
    upstream_resolver_ip: 8.8.8.8
    service_mirrors:
      - real_hostname: example.com
        in_world_service: web.internal
  cross_world:
    mode: FEDERATED
    peers:
      - name: prod-world
        endpoint: world.example.com
        mode: PEERED
```
**But the gateway handler will silently ignore all of it.**

---

### 1.3 AND Profile Configuration Gaps
**File**: `netengine/spec/models.py:380-388` (ANDProfileDef class)

| Field | Type | Default | Purpose | Files | Status |
|-------|------|---------|---------|-------|--------|
| `dynamic_ip` | bool | True | Dynamic IP allocation | models.py:385 | ❌ Never checked |
| `bgp` | str (optional/required/disabled) | None | BGP support level | models.py:388 | ❌ Never validated |
| `reverse_dns` | bool | False | Reverse DNS records | models.py:387 | ❌ Never implemented |

**Evidence**:
```bash
$ grep -r "dynamic_ip\|reverse_dns" netengine/handlers/ netengine/phases/
# Returns: (nothing)
$ grep -n "bgp" netengine/handlers/gateway_handler.py
# Returns: (nothing)
```

**Handler Implementation**: `netengine/handlers/gateway_handler.py` generates nftables rules based only on profile **name** (residential/business/datacenter/airgapped), **ignoring all ANDProfileDef fields**.

**Lines 27-82**: Four hardcoded rule generators, zero reference to `dynamic_ip`, `bgp`, or `reverse_dns` fields.

**Impact**: Users can create custom AND profiles with detailed configurations that are completely ignored:
```yaml
ands:
  profiles:
    custom_profile:
      dhcp: true
      nat: false
      dynamic_ip: false  # <- Ignored
      inbound: allowed
      reverse_dns: true  # <- Ignored
      bgp: required      # <- Ignored
```

---

### 1.4 Mail Configuration Gaps
**File**: `netengine/spec/models.py:442-453` (MailConfig class)

| Field | Type | Default | Purpose | Files | Status |
|-------|------|---------|---------|-------|--------|
| `dkim.enabled` | bool | True | DKIM signing | models.py:422 | ✅ Referenced in mail_handler.py |
| `dkim.key_signing_policy` | Lifecycle | EPHEMERAL | Key storage | models.py:423 | ⚠️ Stored but not enforced |
| `dmarc.enabled` | bool | True | DMARC policies | models.py:429 | ✅ Referenced but incomplete |
| `dmarc.policy` | str | "reject" | DMARC action | models.py:430 | ⚠️ Not validated (allows invalid values) |
| `mailbox_policy.spf_default` | str | "v=spf1 mx -all" | SPF default | models.py:438 | ❌ Never read |
| `mailbox_policy.dmarc_default` | str | "v=DMARC1; p=reject" | DMARC default | models.py:439 | ❌ Never read |

**Evidence**: `netengine/handlers/mail_handler.py` only implements basic Postfix configuration; no SPF/DMARC record generation or validation.

---

## 2. 🟡 MEDIUM: EVENT INFRASTRUCTURE GAPS

### 2.1 Queue Registration Baseline
**File**: `netengine/events/queues.py` (`PRIMARY_QUEUES` definition)

`netengine/events/queues.py::PRIMARY_QUEUES` is the source of truth for the
operator-facing pgmq queue set. The current baseline is 11 primary queues plus
11 matching dead-letter queues (`*_dlq`):

```python
PRIMARY_QUEUES = (
    Queue.DNS_UPDATES,
    Queue.OIDC_PROVISIONING,
    Queue.AND_PROVISIONING,
    Queue.INWORLD_ADMISSIONS,
    Queue.SERVICES_ADMISSIONS,
    Queue.AND_ADMISSIONS,
    Queue.PKI_CERT_ROTATION_EVENTS,
    Queue.DRIFT_EVENTS,
    Queue.WORLD_HEALTH,
    Queue.GATEWAY_PORTAL_EVENTS,
    Queue.PHASE_EVENTS,
)
```

**Registered Primary Queues**:

| Queue Name | Purpose |
|---|---|
| `dns_updates` | DNS zone updates |
| `oidc_provisioning` | Identity setup |
| `and_provisioning` | Network isolation setup |
| `inworld_admissions` | In-world admission events |
| `services_admissions` | Service admission events |
| `and_admissions` | AND admission events |
| `pki_cert_rotation_events` | Certificate rotation events |
| `drift_events` | Drift detection and remediation events |
| `world_health` | Health check events |
| `gateway_portal_events` | Gateway portal lifecycle events |
| `phase_events` | Phase lifecycle events |

**Operator note**: Queue metrics, replay tooling, and DLQ checks should derive
their queue inventory from `PRIMARY_QUEUES` rather than copying a hard-coded
count or list into runbooks.

---

### 2.2 Missing Event Consumers
**Files**: phase_ands.py:342, phase_inworld_identity.py:470, phase_services.py:290

**Pattern**: Event emission is conditioned on pgmq availability. In ephemeral (non-persistent) mode, pgmq is unavailable:

```python
# netengine/phases/phase_ands.py:340-346
if context.pgmq_client:
    await context.pgmq_client.send_to_queue("and_admissions", event)
else:
    logger.warning("pgmq_client not available; org admission events disabled")
    # Code continues silently — no consumers ever process org admissions
```

**Impact**: In ephemeral mode (the default for local testing), org provisioning events are never queued or consumed. The features appear to work (orgs are created) but event-driven architecture is disabled without user awareness.

---

## 3. 🟡 MEDIUM: OPERATOR API GAPS

**File**: `netengine/api/routes.py`

### Missing Modification Endpoints

| Feature | Declared In | Handler Exists? | API Endpoint | Status |
|---------|---|---|---|---|
| AND Profile Changes | spec/models.py | ✅ and_handler.py | ❌ No PUT /ands/{and_name}/profile | Missing |
| Gateway Real Internet Config | spec/models.py | ❌ None | ❌ No endpoint | Missing |
| Gateway Cross-World Config | spec/models.py | ❌ None | ❌ No endpoint | Missing |
| Service Enable/Disable | spec/models.py | ❌ None | ❌ No PUT /services/{name} | Missing |
| PKI Rotation Policy | spec/models.py | ⚠️ Worker exists | ❌ No endpoint | Missing |
| Mail Config Updates | spec/models.py | ⚠️ Partial | ❌ No PUT /services/mail | Missing |

**Example Gap**:
- Handler `and_handler.py:83` can update AND profiles
- But no API endpoint exposes it
- Operators must use CLI or manually edit state file

---

## 4. 🟡 MEDIUM: PHASE PREREQUISITES INCOMPLETE

**File**: `netengine/core/phase_graph.py:39-46`

```python
PHASE_PREREQUISITES: dict[int, list[str]] = {
    3: ["dns_output"],
    4: ["pki_bootstrapped"],
    5: ["identity_platform_output"],
    6: ["world_registry_output", "domain_registry_output"],
    7: ["identity_inworld_output"],
    8: ["ands_output"],
    # Phase 9 is missing prerequisites!
}
```

**Gap**: Phase 9 (OrgAppsPhaseHandler) requires `world_services_output` but doesn't declare it.

**Impact**: If Phase 8 (Services) fails, Phase 9 can still attempt to run. Org apps may fail to deploy if services aren't ready.

---

## 5. 🟡 MEDIUM: PHASE 2 IMPLICIT HANDLING

**File**: `netengine/core/orchestrator.py:202-203`

```python
def _mark_phase_complete(self, phase_num: int, handler: BasePhaseHandler) -> None:
    self.runtime_state.phase_completed[str(phase_num)] = True
    if isinstance(handler, DNSHandler):
        self.runtime_state.phase_completed["2"] = True  # <- Auto-marked!
```

**Issue**:
- Phase 2 is handled **implicitly** by Phase 1 (DNSHandler)
- `PHASE_HANDLERS` omits Phase 2 entirely
- Phase 2 has no dedicated healthcheck
- Cannot retry Phase 2 independently
- If Phase 2 setup partially fails, auto-completion masks the issue

**Impact**: Debugging DNS issues is harder; phase execution flow is non-obvious.

---

## 6. 🟢 SILENT GRACEFUL DEGRADATION

### 6.1 pgmq Unavailability Fallback
**Files**: phase_ands.py:340, phase_inworld_identity.py:470, phase_services.py:290

In ephemeral mode, pgmq is unavailable. Code logs and continues:
```python
logger.warning("pgmq_client not available; org admission events disabled")
# No exception; just silently skipped
```

**Impact**: Users don't realize event infrastructure is disabled.

### 6.2 Queue Creation Deferral
**File**: `netengine/cli/main.py:356`

```python
# When registering queues
if queue_already_exists:
    pass  # queue may not exist yet — non-fatal
```

**Impact**: Queues may not be created, causing runtime failures during event emission.

---

## 7. 🔴 STATE/SCHEMA DEBT

**File**: `netengine/core/state.py:51-72`

**Deprecated Container Tracking Fields** (never read, only written):

| Field | Lines | Set By | Read By | Usage |
|---|---|---|---|---|
| `gateway_container_id` | 52 | substrate handler | (none) | ❌ Unused |
| `dns_root_container_id` | 53 | dns handler | (none) | ❌ Unused |
| `step_ca_container_id` | 55 | pki handler | (none) | ❌ Unused |
| `keycloak_platform_container_id` | 57 | identity handler | (none) | ❌ Unused |
| `inworld_keycloak_container_id` | 60 | inworld handler | (none) | ❌ Unused |
| `bootstrap_admin_password` | 63 | platform identity | (none) | ❌ Unused |
| `platform_client_id` | 64 | identity handler | (none) | ❌ Unused |

**Modern Approach**: Phase outputs are dicts (e.g., `pki_output["container_id"]`), not individual state fields.

**Impact**: State file bloat; code is confusing (old vs. new tracking patterns).

---

## 8. 🟢 TEST COVERAGE GAPS

**File**: `tests/integration/`

**Features with Zero Test Coverage**:

| Feature | Declared In | Test File | Coverage |
|---|---|---|---|
| Real Internet Mode | GatewayPortal | (none) | 0% |
| Cross-World Peering | GatewayPortal | (none) | 0% |
| Service Mirrors | RealInternetConfig | (none) | 0% |
| BGP Fabric | ANDsPhase | (none) | 0% |
| DNSSEC | PKIPhase | (none) | 0% |
| OCSP | PKIPhase | (none) | 0% |
| CRL | PKIPhase | (none) | 0% |
| Intermediate CA | PKIPhase | (none) | 0% |
| AND Dynamic IP | ANDProfileDef | (none) | 0% |
| AND Reverse DNS | ANDProfileDef | (none) | 0% |
| DMARC Policy | MailConfig | (none) | 0% |
| SPF Records | MailConfig | (none) | 0% |
| PKI Rotation Policy | PKIRotationPolicy | (none) | 0% |

**Contrast**: Phases 1-8 have 10+ integration tests each; Phase 9 (OrgApps) has partial coverage.

---

## 9. 🟢 WORKER REGISTRATION GAPS

**File**: `netengine/handlers/phase_pki.py:127-133`

Only PKI rotation worker is auto-registered:
```python
def _register_rotation_worker(self, context, pki, spec):
    worker = PKICertRotationWorker(pki, context.pgmq_client, ...)
    context.consumer_supervisor.register_worker(worker)
```

**Missing Auto-Registration**:
- Drift detection auto-healing worker
- Health monitoring worker
- Event queue watcher/DLQ replay worker

---

## 10. 🔴 WORLD_SPEC PERSISTENCE INCONSISTENCY

**File**: `netengine/core/state.py:62` and `netengine/api/routes.py:99`

**Issue**: `world_spec` is stored in RuntimeState but not updated by `/api/v1/reload`.

```python
# routes.py:99
old_spec = NetEngineSpec(**state.world_spec)  # Uses stale snapshot
# But reload endpoint doesn't update state.world_spec after changes
```

**Impact**: If an operator reloads the spec, the stored `world_spec` in state becomes stale. Subsequent queries return outdated config.

---

## 11. 📊 PRIORITY FIX ROADMAP

### 🔴 P0: Fix Immediately (Spec Honesty)
1. **Add warnings for unsupported spec fields**
   - Detect when users set dnssec_enabled, crl_enabled, etc.
   - Log a clear warning: "Feature not yet implemented; see GitHub issue #XXX"
   - **Effort**: 1-2 hours
   - **Benefit**: Users aren't confused

2. **Document which features are supported**
   - Update README.md with explicit "Supported v1.0" vs. "Planned v1.1+" features
   - **Effort**: 1 hour
   - **Benefit**: Manages expectations

### 🟡 P1: High Value Gaps (3-5 Hours Each)
3. **Maintain Queue Registration Baseline**
   - Keep `PRIMARY_QUEUES` as the source of truth for the 11 primary queues
   - Ensure ConsumerSupervisor creates each primary queue and its matching DLQ
   - **Files**: events/queues.py, core/consumer_supervisor.py
   - **Effort**: 2 hours

4. **Add Phase 9 Prerequisites**
   - Add `8: ["world_services_output"]` to PHASE_PREREQUISITES
   - Add healthcheck for this prerequisite
   - **Files**: core/phase_graph.py, handlers/app_handler.py
   - **Effort**: 1 hour

5. **Implement Intermediate CA Support**
   - Wire `intermediate_ca_enabled` to step-ca init
   - Add tests
   - **Files**: handlers/pki_handler.py, handlers/phase_pki.py, tests/
   - **Effort**: 4 hours
   - **Benefit**: Enables proper cert hierarchy

6. **Wire PKI Rotation Policy from Spec**
   - Parse `spec.pki.rotation_policy` in phase_pki.py
   - Pass cert-type configs to worker registration
   - **Files**: handlers/phase_pki.py, spec/models.py
   - **Effort**: 2-3 hours
   - **Benefit**: Users control rotation via YAML

7. **Update world_spec on Reload**
   - Sync `state.world_spec` after successful reload
   - **Files**: api/routes.py, core/reload.py
   - **Effort**: 1 hour

8. **Add Missing API Endpoints**
   - PUT /ands/{and_name}/profile
   - PUT /services/{name}
   - **Files**: api/routes.py
   - **Effort**: 2-3 hours

### 🟢 P2: Nice-to-Have (5-10 Hours)
9. **Fix Phase 2 Explicit Handling**
   - Create dedicated Phase2Handler or decompose DNSHandler
   - Add independent Phase 2 healthcheck
   - **Effort**: 5-6 hours

10. **Clean Up Deprecated State Fields**
    - Remove container ID fields; use output dicts only
    - Update all handlers
    - **Effort**: 3 hours
    - **Benefit**: State file cleaner; code clearer

11. **Add Test Coverage for Declared Features**
    - At minimum, tests that verify unsupported features are gracefully ignored
    - **Effort**: 4-5 hours

---

## 12. 📋 SUMMARY BY CATEGORY

| Category | Count | Most Critical | Easy Win |
|----------|-------|---|---|
| **Spec Fields (Declared, Not Implemented)** | 14+ | DNSSEC, CRL, OCSP, intermediate CA | Add warnings |
| **Event Infrastructure** | 4 | Queue/DLQ inventory drift | Keep docs and tooling sourced from PRIMARY_QUEUES |
| **API Gaps** | 4+ | Service toggle endpoint | Add PUT endpoints |
| **Phase Logic** | 2 | Phase 9 prerequisites, Phase 2 explicit | Update graph, decompose handler |
| **State Debt** | 7 | Container ID fields | Clean up deprecated fields |
| **Test Coverage** | 8+ | Real internet, cross-world, PKI features | Add integration tests |

---

## 13. 🎯 RECOMMENDED NEXT STEPS

**If High-Risk (production):**
1. Fix queue registration (P1) → prevents runtime failures
2. Add field validation warnings (P0) → manages expectations
3. Fix Phase 9 prerequisites (P1) → prevents partial deployments

**If Quality/Completeness (maintainability):**
1. Clean deprecated state fields (P2)
2. Add test coverage for declared features (P2)
3. Document supported vs. planned features (P0)

**If Feature-Driven (user needs):**
1. Implement intermediate CA (P1, high user value)
2. Wire PKI rotation policy (P1, improves ops)
3. Add gateway config API endpoints (P1, enables hybrid worlds)

---

## 14. 📂 DETAILED FILE LIST FOR QUICK ACTION

| File | Lines | Action |
|------|-------|--------|
| netengine/events/queues.py | 27-33 | Add missing queue names |
| netengine/core/phase_graph.py | 39-46 | Add Phase 9 prerequisite |
| netengine/spec/models.py | 201-228 | Add deprecation notes or implement |
| netengine/api/routes.py | 1-800 | Add missing PUT endpoints |
| netengine/handlers/pki_handler.py | 62-96 | Wire intermediate_ca_enabled |
| netengine/handlers/phase_pki.py | 127-133 | Wire rotation_policy config |
| netengine/core/state.py | 51-72 | Remove deprecated fields |
| tests/integration/ | Various | Add test cases for declared features |

---

## Conclusion

NetEngine has **30+ declared features that are partially or completely unimplemented**, with **four major infrastructure gaps**:

1. **Spec-Implementation Mismatch** (PKI, gateway, AND profiles)
2. **Event Infrastructure Inconsistency** (undefined queues, missing consumers)
3. **API Endpoint Gaps** (no service/gateway config endpoints)
4. **State/Phase Logic Debt** (deprecated fields, implicit Phase 2 handling)

**Most important fix**: Add warnings when users enable unsupported features (1-2 hours, prevents confusion).

**Highest ROI fix**: Implement intermediate CA support (4 hours, enables enterprise use cases).

**Biggest risk**: Queue registration mismatch could cause runtime failures in persistent mode (2 hours to fix, critical to do).
