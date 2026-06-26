# NetEngine Compose.yml Brainstorm

A collection of specialized Docker Compose configurations for different workflows and testing scenarios.

## Compose File Catalog

### Core/Foundation
- **docker-compose.yml** (exists) — Production-like: Postgres + pgmq, Keycloak, NetEngine API
- **docker-compose.dev.yaml** (exists) — Dev lightweight variant

### Testing & CI
- **compose.test-minimal.yml** — CI runner: Postgres only, no Keycloak, pgmq, mock mode
- **compose.test-integration.yml** — Full integration test: Postgres + pgmq + Keycloak + CoreDNS stub
- **compose.test-network.yml** — Network testing: CoreDNS, nftables lab, network policies

### Observability & Debugging
- **compose.observability.yml** — Stack: Prometheus, Grafana, Loki, Jaeger; add to main via `docker compose -f docker-compose.yml -f compose.observability.yml up`
- **compose.debug.yml** — Debug tools: netcat, tcpdump, mitmproxy, curl debugging containers
- **compose.profile-api.yml** — Profiling: Postgres with pgStatements, slow query logs, timing instrumentation

### Data & Persistence
- **compose.backup-test.yml** — Postgres backup/restore scenarios: main db + backup container + S3-compatible (MinIO)
- **compose.multidb.yml** — Multi-version Postgres (15, 16) for migration testing
- **compose.state-replay.yml** — State file replay: Postgres + volume mount for `netengines_state.json` history

### Identity & OIDC
- **compose.keycloak-multi-realm.yml** — Keycloak multi-realm testing (platform + multiple org realms)
- **compose.oauth-provider-test.yml** — Multiple OIDC providers for federation testing

### Services & Mail
- **compose.mail-visual.yml** — Postfix + Mailhog (visual inbox testing)
- **compose.storage-multi.yml** — MinIO + S3-compatible endpoints for storage testing

### Chaos & Resilience
- **compose.chaos-network.yml** — Toxiproxy for latency/failure injection
- **compose.chaos-db.yml** — Postgres with deliberate slowness, connection limits, failover testing
- **compose.resource-constrained.yml** — Low-resource test: CPU/mem limits on all services

### Scaling & Load
- **compose.load-test.yml** — K6 + Grafana + Postgres; orchestrate load generation
- **compose.benchmarks.yml** — Performance baseline: pgbench, DNS query profiler, cert issuance timing

### Security & Audit
- **compose.audit.yml** — Postgres audit logging, PKI audit trail, event log collection
- **compose.security-scan.yml** — Trivy, image scanning, vulnerability checks

### Federation & Multi-World
- **compose.multi-world.yml** — Two separate NetEngine instances with DNS federation
- **compose.world-bridge.yml** — Network bridging between worlds, cross-world lookup

### Special Scenarios
- **compose.offline.yml** — Air-gapped setup; no external image pulls, local registries
- **compose.arm64.yml** — ARM64 variants (if not all services have ARM images)
- **compose.gpu.yml** — GPU-accelerated services if applicable

---

## Usage Patterns

### 1. Running with observability overlay
```bash
docker compose -f docker-compose.yml -f compose.observability.yml up -d
netengine up examples/minimal.yaml
# Access Grafana at localhost:3000, Jaeger at localhost:6831
```

### 2. Integration tests with full stack
```bash
docker compose -f compose.test-integration.yml up -d
pytest tests/integration/
docker compose -f compose.test-integration.yml down
```

### 3. Chaos engineering session
```bash
docker compose -f docker-compose.yml -f compose.chaos-network.yml -f compose.chaos-db.yml up -d
# Toxiproxy intercepts Postgres at toxiproxy:5432, adds latency/failures
# Applications connect to toxiproxy instead of postgres:5432
```

### 4. Load testing campaign
```bash
docker compose -f compose.load-test.yml up -d
# K6 script runs in container, writes metrics to Prometheus
# Watch live in Grafana dashboard
```

---

## Compose File Template Snippets

### Reusable Healthcheck for Service X
```yaml
healthcheck:
  test: ["CMD-SHELL", "specific_test_command"]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 20s
```

### Environment variable injection patterns
```yaml
environment:
  NETENGINE_DB_URL: postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}
  NETENGINE_MOCK: ${NETENGINE_MOCK:-false}
  LOG_LEVEL: ${LOG_LEVEL:-INFO}
```

### Service profiles (selective startup)
```yaml
services:
  debug_container:
    image: ubuntu:latest
    profiles:
      - debug  # Only start with: docker compose --profile debug up
```

---

## Design Decisions

1. **Modular by concern** — Each compose file addresses one testing/operational concern
2. **Composable** — Use `docker compose -f base.yml -f overlay.yml` to combine
3. **State isolation** — Each variant can have its own volume/network prefix
4. **CI-friendly** — All include health checks and startup conditions
5. **Documentation** — Each file will have comments explaining service roles

---

## Questions to Resolve

- [ ] Should we parameterize image versions (tag via env var)?
- [ ] Should compose files auto-generate SSL certs for local testing?
- [ ] Should we provide `compose.override.yml` as a git-ignored template?
- [ ] Do we want dedicated profiles for: `debug`, `load-test`, `chaos`, `security`?
- [ ] Should multi-world compose use separate networks or shared?
- [ ] Should we version compose file format or stick to 3.8+?
