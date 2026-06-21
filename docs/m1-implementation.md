# M1 Implementation: Substrate + DNS (Phases 0-2 Handlers)

## Overview

M1 implements Phase 0 (Substrate) and Phases 1-2 (DNS) handler infrastructure for NetEngine. These handlers initialize the foundational container infrastructure and authoritative DNS hierarchy before higher-level services are deployed in M2+.

**Status:** ✅ Complete  
**Test Coverage:** 22 new tests, all passing  
**Code Quality:** flake8 ✓ mypy ✓

---

## Phase 0: Substrate Handler

**Responsibility:** Pre-naming, pre-PKI infrastructure setup

### What it Does

1. **Orchestrator Initialization**
   - Supports Docker Swarm (M1) and Kubernetes (future)
   - Stubs orchestrator API calls; real implementation in M4+
   - Returns orchestrator type, status, version

2. **Container Network Creation**
   - Creates platform network (172.20.0.0/16 by default)
   - Creates core network (10.0.0.0/8 by default)
   - Configurable via spec.substrate.networks
   - Returns network IDs, types, subnets

3. **NTP Synchronization**
   - Configures NTP servers (default: pool.ntp.org)
   - Conditional (enabled by spec.substrate.ntp.enabled)
   - Returns synchronization status

4. **Gateway Network Stubs**
   - Verifies gateway has platform and core IP addresses
   - Sets up boundary for later policy application (Phase 7)
   - Returns gateway network configuration

5. **Event Emission**
   - Emits `substrate.initialized` event with:
     - orchestrator type
     - networks count
     - NTP enabled flag

### Handler Interface

```python
class SubstrateHandler(BasePhaseHandler):
    async def execute(context: PhaseContext) -> None
    async def healthcheck(context: PhaseContext) -> bool
    async def should_skip(context: PhaseContext) -> bool
```

### Output Structure

```python
runtime_state.substrate_output = {
    "orchestrator": {
        "type": "docker_swarm",
        "status": "ready",
        "healthy": True,
        "version": "24.0+",
        "initialized_at": "2026-06-21T12:34:56..."
    },
    "networks": {
        "platform": {
            "name": "platform",
            "id": "mock-net-platform",
            "type": "bridge",
            "subnet": "172.20.0.0/16",
            "created_at": "2026-06-21T12:34:56..."
        },
        "core": { ... }
    },
    "ntp": {
        "enabled": True,
        "servers": ["pool.ntp.org"],
        "synchronized": True,
        "stratum": 2,
        "configured_at": "2026-06-21T12:34:56..."
    },
    "gateway": {
        "platform_ip": "172.20.0.1",
        "core_ip": "10.0.0.1",
        "status": "ready",
        "created_at": "2026-06-21T12:34:56..."
    },
    "deployed_at": "2026-06-21T12:34:56..."
}
```

### Idempotence

- `should_skip()` returns `True` if substrate already deployed
- Safe to call multiple times (idempotent)
- Supports reload scenarios in persistent mode

---

## Phases 1-2: DNS Handler

**Responsibility:** Authoritative DNS root and zone hierarchy setup

### What it Does

1. **Root Zone Setup**
   - Creates authoritative root zone (root.internal)
   - Configures SOA and NS records
   - Supports configurable serial policy (timestamp, manual)
   - Returns root zone configuration

2. **Platform Zone Configuration**
   - Creates platform zone (platform.internal)
   - Reserved for L1 service names:
     - auth.platform.internal (identity provider)
     - ca.platform.internal (ACME server)
     - registry.platform.internal (world registry)
     - etc.
   - Returns platform zone config

3. **TLD Server Setup**
   - Configures TLD servers from spec.dns.tlds
   - Each TLD has its own authoritative server
   - Set up for zone delegation from root
   - Returns TLD configuration map

4. **Zone File Generation**
   - Generates RFC 1035 zone files for all zones
   - Root zone: Delegates to platform zone and TLDs via NS records
   - Platform zone: Contains L1 service A records (stub)
   - TLD zones: Start empty, populated by domain registry (Phase 5b)
   - SOA records with configurable serial policy
   - Proper NS record delegation chain

5. **DNS Service Verification**
   - Checks all zones generated successfully
   - Verifies zone files contain required SOA/NS records
   - Returns health status
   - M1: Stub verification (real queries in M4+)

6. **Event Emission**
   - Emits `dns.zones_ready` event with:
     - root zone name
     - platform zone name
     - TLD count

### Handler Interface

```python
class DNSHandler(BasePhaseHandler):
    async def execute(context: PhaseContext) -> None
    async def healthcheck(context: PhaseContext) -> bool
    async def should_skip(context: PhaseContext) -> bool
```

### Output Structure

```python
runtime_state.dns_output = {
    "root_zone": {
        "enabled": True,
        "name": "root.internal",
        "type": "authoritative",
        "server": "coredns",
        "listen_ip": "10.0.0.2",
        "soa_primary_ns": "root.internal",
        "soa_email": "admin.internal",
        "serial_policy": "timestamp",
        "deployed_at": "2026-06-21T12:34:56..."
    },
    "platform_zone": {
        "name": "platform.internal",
        "type": "authoritative",
        "listen_ip": "10.0.0.3",
        "ns_server": "ns.platform.internal",
        "deployed_at": "2026-06-21T12:34:56..."
    },
    "tlds": {
        "internal": {
            "name": "internal",
            "type": "authoritative",
            "listen_ip": "10.0.0.4",
            "ns_server": "ns4.internal",
            "deployed_at": "2026-06-21T12:34:56..."
        }
    },
    "zone_files": {
        "root.internal": "; Root zone: root.internal\n...",
        "platform.internal": "; Platform zone: platform.internal\n...",
        "internal": "; TLD zone: internal\n..."
    },
    "healthy": True,
    "deployed_at": "2026-06-21T12:34:56..."
}
```

### Zone File Example

**Root zone (root.internal):**
```
; Root zone: root.internal
; Generated: 2026-06-21T12:34:56.123456
root.internal. SOA root.internal. admin.internal. 1719057296 3600 1800 604800 86400
root.internal. NS ns.root.internal.

; Delegation to platform zone
platform.internal. NS ns.platform.internal.
platform.internal. A 10.0.0.3

; Delegations to TLD servers
internal. NS ns4.internal.
ns4.internal. A 10.0.0.4
```

**Platform zone (platform.internal):**
```
; Platform zone: platform.internal
; Generated: 2026-06-21T12:34:56.123456
platform.internal. SOA root.internal. root.internal. 1 3600 1800 604800 86400
platform.internal. NS ns.platform.internal.
ns.platform.internal. A 10.0.0.3

; L1 service records (populated by M4+ handlers)
auth.platform.internal. A 10.0.0.7
ca.platform.internal. A 10.0.0.6
registry.platform.internal. A 10.0.0.8
```

### Idempotence

- `should_skip()` returns `True` if DNS already deployed
- Safe to call multiple times
- Supports reload scenarios

---

## Architecture Decisions

### Event Tracing (M0 Locked)

Both handlers emit events with correlation IDs for causality tracing:

```python
event = EventEnvelope.create(
    event_type="substrate.initialized" | "dns.zones_ready",
    emitted_by="substrate_handler" | "dns_handler",
    payload={...},
    correlation_id=context.runtime_state.correlation_id,
    parent_event_id=context.runtime_state.parent_event_id,
)
```

- Correlation IDs preserved across handler chain
- M1: Events logged only
- M4+: Events queued to pgmq for async handler communication

### Execution Order

```
M1 Phases → M2+ Phases
├─ Phase 0: Substrate (substrate_handler)
├─ Phase 1-2: DNS (dns_handler)
├─ Phase 3: PKI (pki_handler)
├─ ...Phase 8: Services
└─ M9+: Federation features
```

### Service Stubs (M1-M3)

Substrate and DNS handlers stub real service calls:

- **Phase 0 Stub:** Docker network creation, orchestrator init
  - Real implementation: Docker/K8s API calls in M4+
  
- **Phases 1-2 Stub:** Zone file generation, DNS verification
  - Real implementation: CoreDNS container deployment in M4+

Stubs ensure:
- Spec validation works end-to-end
- Output structures are defined early
- Handlers can be tested without external services
- Real service integration straightforward (M4+)

---

## Testing

### Test Coverage (22 tests)

**Substrate Handler (8 tests):**
- `test_execute_creates_output` — Output populated
- `test_execute_creates_networks` — Networks created with correct subnets
- `test_execute_configures_ntp` — NTP configured if enabled
- `test_execute_sets_timestamps` — Timestamps set correctly
- `test_healthcheck_passes_after_execute` — Health check passes
- `test_healthcheck_fails_before_execute` — Health check fails before execution
- `test_should_skip_true_after_execute` — Skip returns True after deploy
- `test_should_skip_false_before_execute` — Skip returns False before deploy

**DNS Handler (12 tests):**
- `test_execute_creates_output` — Output populated
- `test_execute_creates_root_zone` — Root zone setup correct
- `test_execute_creates_platform_zone` — Platform zone setup correct
- `test_execute_creates_tlds` — TLDs created from spec
- `test_execute_generates_zone_files` — Zone files generated
- `test_zone_files_contain_soa_records` — SOA records present
- `test_zone_files_contain_ns_records` — NS records present
- `test_execute_marks_healthy` — Service marked healthy
- `test_healthcheck_passes_after_execute` — Health check passes
- `test_healthcheck_fails_before_execute` — Health check fails before execution
- `test_should_skip_true_after_execute` — Skip returns True after deploy
- `test_should_skip_false_before_execute` — Skip returns False before deploy

**Integration (2 tests):**
- `test_substrate_then_dns` — Correct execution order
- `test_correlation_ids_preserved` — Correlation IDs preserved across handlers

### Running Tests

```bash
poetry run pytest tests/test_m1_handlers.py -v
```

All 37 tests pass (15 M0 + 22 M1):
- Substrate handler: 8/8 ✓
- DNS handler: 12/12 ✓
- Integration: 2/2 ✓
- M0 spec parsing: 15/15 ✓

### Code Quality

```bash
poetry run flake8 netengine/handlers/substrate.py netengine/handlers/dns.py
# Result: ✓ (no issues)

poetry run mypy netengine/handlers/substrate.py netengine/handlers/dns.py
# Result: Success: no issues found
```

---

## Usage Example

```python
from netengine.handlers.substrate import SubstrateHandler
from netengine.handlers.dns import DNSHandler
from netengine.handlers.context import PhaseContext, RuntimeState
from netengine.spec.loader import load_spec

# Load spec
spec = load_spec("examples/minimal.yaml")

# Create execution context
state = RuntimeState()
context = PhaseContext(
    spec=spec,
    runtime_state=state,
    logger=get_logger("netengine")
)

# Execute Phase 0: Substrate
substrate = SubstrateHandler()
await substrate.execute(context)

# Execute Phases 1-2: DNS
dns = DNSHandler()
await dns.execute(context)

# Access outputs
print(context.runtime_state.substrate_output)
print(context.runtime_state.dns_output)
```

---

## Future Work (M2+)

### M2: PKI Handler
- Deploy root CA and ACME provisioner
- Generate signing keys
- Populate ca.platform.internal DNS record

### M3+: Higher Phase Handlers
- M4: Identity Platform (Keycloak)
- M5: Registries (world & domain)
- M6: In-world Identity
- M7: ANDs (Administrative Network Domains)
- M8: World Services (mail, storage)

### M4+: Real Service Integration
- Replace substrate stubs with Docker API calls
- Deploy CoreDNS container with zone files
- Implement real DNS verification (dig queries)
- Queue events to pgmq for async communication

### M9+: Cross-world Federation
- Extend DNS with cross-world peer zones
- Correlation IDs bridge events across worlds
- Federated zone delegation model

---

## Files Modified/Created

**Created:**
- `netengine/handlers/substrate.py` — Phase 0 substrate handler (340 lines)
- `netengine/handlers/dns.py` — Phases 1-2 DNS handler (515 lines)
- `tests/test_m1_handlers.py` — M1 handler tests (325 lines)

**Modified:**
- `netengine/handlers/__init__.py` — Export new handlers

**Total:**
- 1,180 lines of implementation
- 325 lines of tests
- All with proper type hints, docstrings, linting

---

## Key Design Decisions

1. **Stub vs. Real Services**
   - M1-M3: Stubs (logic only)
   - M4+: Real service integration
   - Rationale: Enables spec validation and testing early

2. **Zone File Generation**
   - RFC 1035 format (compatible with BIND, CoreDNS, etc.)
   - Generated at deployment time
   - Enables dynamic zone updates in M5+ (domain registry)

3. **Idempotent Execution**
   - `should_skip()` prevents duplicate work
   - Safe for restart scenarios
   - Supports persistent mode reloads

4. **Event Tracing**
   - Correlation IDs baked in from M1
   - No retrofit needed for M4+ pgmq integration
   - Enables operator visibility across handler chains

5. **Network Isolation**
   - platform network (172.20.0.0/16): Operator/management plane
   - core network (10.0.0.0/8): Org/workload plane
   - Gateway boundary enforced in Phase 7 (AND policies)

---

## Milestone Checklist

- [x] SubstrateHandler implements BasePhaseHandler interface
- [x] DNSHandler implements BasePhaseHandler interface
- [x] Both handlers idempotent (execute, skip, healthcheck)
- [x] Zone file generation (RFC 1035)
- [x] Event envelope creation with correlation IDs
- [x] All 22 tests passing
- [x] Code quality (flake8, mypy)
- [x] Comprehensive docstrings
- [x] Integration tests (execution order, correlation IDs)
- [x] Committed and pushed to branch

**M1 Complete** ✅
