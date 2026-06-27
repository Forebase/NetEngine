# NetEngine Compose.yml Brainstorm

A collection of 20+ specialized Docker Compose configurations for different workflows and testing scenarios.

**Status**: ✅ = implemented, — = planned/future

## Quick Stats
- **Total Variants**: 20+
- **Implemented**: 17 compose files
- **Config/Script Templates**: 10+
- **Use Cases Covered**: 
  - 🧪 Testing & CI
  - 📊 Observability (metrics, logs, traces, exports)
  - 💾 Data & persistence (backup, databases, state)
  - ⚡ Caching & queues (Redis, RabbitMQ, Kafka)
  - 🔒 Security & compliance (auditing, scanning, secrets)
  - 🌐 Network & SSL/TLS (termination, mTLS, chaos injection)
  - 📈 Load testing & performance (K6, resource constraints)
  - 🛠️ Development (hot-reload, debugging, docs)
  - 🌍 Multi-world & federation

## Compose File Catalog

### Core/Foundation
- **docker-compose.yml** (exists) — Production-like: Postgres + pgmq, Keycloak, NetEngine API
- **docker-compose.dev.yaml** (exists) — Dev lightweight variant

### Testing & CI
- **compose.test-minimal.yml** — CI runner: Postgres only, no Keycloak, pgmq, mock mode
- **compose.test-integration.yml** — Full integration test: Postgres + pgmq + Keycloak + CoreDNS stub
- **compose.test-network.yml** — Network testing: CoreDNS, nftables lab, network policies

### Observability & Debugging
- **compose.observability.yml** ✅ — Stack: Prometheus, Grafana, Loki, Jaeger
- **compose.debug.yml** ✅ — Debug tools: tcpdump, mitmproxy, dig, netshoot, Postgres query analyzer
- **compose.exporters.yml** ✅ — Metrics exporters: postgres-exporter, docker-exporter, node-exporter
- **compose.tracing.yml** ✅ — Distributed tracing: Jaeger, Zipkin, Tempo, Elasticsearch

### Data & Persistence
- **compose.backup-recovery.yml** ✅ — Backup/restore: MinIO S3-compatible, WAL archival, PITR testing, backup validation
- **compose.database-variants.yml** ✅ — Multi-version: Postgres 15, 16, replicas, TimescaleDB, resource-constrained
- **compose.state-replay.yml** — State file replay: Postgres + volume mount for `netengines_state.json` history

### Caching & Message Queues
- **compose.cache-redis.yml** ✅ — Redis: primary + replica + Sentinel HA, metrics exporter
- **compose.message-queues.yml** ✅ — RabbitMQ, Kafka + Zookeeper, Kafka UI, Redis Streams

### Identity & OIDC
- **compose.keycloak-multi-realm.yml** — Keycloak multi-realm testing (platform + multiple org realms)
- **compose.oauth-provider-test.yml** — Multiple OIDC providers for federation testing

### Services & Mail
- **compose.mail-visual.yml** ✅ — Postfix + Mailhog (visual inbox testing)
- **compose.storage-multi.yml** — MinIO + S3-compatible endpoints for storage testing

### Network & SSL/TLS
- **compose.ssl-testing.yml** ✅ — Nginx TLS termination, mTLS, Let's Encrypt, cert monitoring
- **compose.chaos-network.yml** ✅ — Toxiproxy: latency, jitter, packet loss, connection resets, timeouts
- **compose.chaos-db.yml** — Postgres slowness, connection limits, failover testing

### Scaling & Load Testing
- **compose.load-test.yml** ✅ — K6 + Prometheus + Grafana for orchestrated load generation
- **compose.resource-constrained.yml** ✅ — CPU/memory limits, Alpine minimal images, slow disk/network
- **compose.benchmarks.yml** — Performance baseline: pgbench, DNS profiler, cert timing

### Security & Audit
- **compose.audit.yml** ✅ — Postgres pgAudit, audit logs, Loki collection, Grafana dashboards
- **compose.security.yml** ✅ — Trivy, OWASP Dependency Check, Snyk, SonarQube, Falco, OWASP ZAP, Vault, gitleaks

### Development
- **compose.dev-hotreload.yml** ✅ — Hot-reload on code changes, debugpy, test watcher, API docs server

### Federation & Multi-World
- **compose.multi-world.yml** — Two separate NetEngine instances with DNS federation
- **compose.world-bridge.yml** — Network bridging between worlds, cross-world lookup

### In-World Platform Services (13 variants) ✨
High-level services that run **within** a NetEngine world to provide platform infrastructure.

**Core Platform:**
- **compose.search-engine.yml** ✅ — Full-text search (Elasticsearch, Kibana, Meilisearch, indexer)
- **compose.domain-registrar.yml** ✅ — Domain registry & management, WHOIS, DNS delegation
- **compose.api-gateway.yml** ✅ — API routing & rate limiting (Kong, Envoy, Konga UI)
- **compose.service-catalog.yml** ✅ — Service discovery & registry (Consul, Istio, OpenSearch)

**Developer Experience:**
- **compose.knowledge-base.yml** ✅ — Wiki & documentation (MediaWiki, Bookstack, Sphinx)
- **compose.marketplace.yml** ✅ — App marketplace (npm, Helm, Docker registries, Verdaccio)
- **compose.forms.yml** ✅ — Form builder & surveys (Formspree, response analytics)

**Operations:**
- **compose.analytics.yml** ✅ — BI & analytics (Metabase, Superset, Jupyter, TimescaleDB)
- **compose.messaging.yml** ✅ — Chat & notifications (Mattermost, Rocket.Chat, SMTP)
- **compose.media-hosting.yml** ✅ — Media CDN (MinIO, image/video processing, nginx cache)
- **compose.billing.yml** ✅ — Billing, metering, invoicing, cost tracking
- **compose.resource-manager.yml** ✅ — Quota & capacity management, alerting
- **compose.federation.yml** ✅ — Cross-world federation, peer discovery, user sync

### Special Scenarios
- **compose.offline.yml** — Air-gapped setup; no external image pulls, local registries
- **compose.arm64.yml** — ARM64 variants (if not all services have ARM images)
- **compose.gpu.yml** — GPU-accelerated services if applicable

---

## In-World Services: Building Complete Worlds

The **in-world platform services** are designed to run *inside* a deployed NetEngine world and provide high-level infrastructure:

```
                    ┌─────────────────────────────────────┐
                    │   NetEngine World (running)         │
                    │                                     │
    ┌───────────────┼─────────────────────────────────┼──┐
    │               │   Core (Phase 0-8)              │  │
    │  ┌──────────┐ │ DNS | PKI | Keycloak | nftables│  │
    │  │ Platform │ │ Domain Registry | Mail | MinIO  │  │
    │  │ Services │ │                                 │  │
    │  │ (this    │ │  ┌──────────────────────────┐  │  │
    │  │ section) │ │  │ Optional Platform Layer: │  │  │
    │  │          │ │  │ • Search engine          │  │  │
    │  │ • Search │ │  │ • API gateway            │  │  │
    │  │ • Registrar  │  │ • Service catalog       │  │  │
    │  │ • Billing    │  │ • Wiki/docs             │  │  │
    │  │ • Analytics  │  │ • Marketplace           │  │  │
    │  │ • Chat       │  │ • Chat/messaging        │  │  │
    │  │ • Marketplace│  │ • Media hosting         │  │  │
    │  │ • Media CDN  │  │ • Forms & surveys       │  │  │
    │  └──────────────┼──────────────────────────┘  │  │
    │                │                               │  │
    └────────────────┼───────────────────────────────┴──┘
                     │
              [Docker Compose overlays]
```

Enable any combination of these with Docker Compose profiles:

```bash
# Minimal world (core phases only)
netengine up examples/minimal.yaml

# Platform + search
docker compose -f docker-compose.yml -f compose/compose.search-engine.yml up -d

# Full-featured world
docker compose \
  -f docker-compose.yml \
  -f compose/compose.search-engine.yml \
  -f compose/compose.api-gateway.yml \
  -f compose/compose.marketplace.yml \
  -f compose/compose.analytics.yml \
  -f compose/compose.messaging.yml \
  up -d
```

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
