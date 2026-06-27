# NetEngine Compose Configurations

A modular collection of Docker Compose files for different workflows, testing scenarios, and operational setups.

## Quick Reference

| File | Purpose | Use Case |
|------|---------|----------|
| `compose.test-minimal.yml` | Postgres only (CI-friendly) | Unit tests, fast CI runs |
| `compose.observability.yml` | Prometheus, Grafana, Loki, Jaeger | Monitoring, debugging, tracing |
| `compose.load-test.yml` | K6 load testing orchestration | Performance testing, capacity planning |
| `compose.chaos-network.yml` | Toxiproxy for failure injection | Resilience testing, chaos engineering |
| `compose.mail-visual.yml` | Mailhog + Postfix | Email integration testing |
| `compose.multi-world.yml` | Two independent NetEngine instances | Federation testing, cross-world scenarios |
| `compose.audit.yml` | Postgres with pgAudit, audit logs | Security auditing, compliance |

## Usage Patterns

### 1. Development with Observability

```bash
# Start core services + monitoring stack
docker compose -f docker-compose.yml -f compose/compose.observability.yml up -d

# View dashboards
# Grafana: http://localhost:3000
# Prometheus: http://localhost:9090
# Jaeger UI: http://localhost:16686

# Boot NetEngine world
netengine up examples/minimal.yaml

# Watch metrics in real-time
```

### 2. Integration Testing

```bash
# Minimal test environment
docker compose -f compose/compose.test-minimal.yml up -d

# Run integration tests
pytest tests/integration/ -v

# Teardown
docker compose -f compose/compose.test-minimal.yml down -v
```

### 3. Load Testing Campaign

```bash
# Start infrastructure + K6 runner
docker compose -f docker-compose.yml -f compose/compose.observability.yml -f compose/compose.load-test.yml up -d

# K6 runs automatically in the k6 container
# Results stream to Prometheus

# Monitor in Grafana (http://localhost:3000)
# K6 dashboard > look for k6_* metrics

# View summary
docker logs netengine_k6_runner
```

### 4. Chaos Engineering Session

```bash
# Start with Toxiproxy intercepting Postgres + Keycloak
docker compose -f docker-compose.yml -f compose/compose.chaos-network.yml up -d

# Apply chaos scenarios (optional)
docker compose -f docker-compose.yml -f compose/compose.chaos-network.yml --profile chaos-control up

# Applications should connect to:
#   postgres: toxiproxy:5432 (instead of postgres:5432)
#   keycloak: toxiproxy:8180 (instead of keycloak:8180)

# Toxiproxy API: http://localhost:8474
# List proxies: curl http://localhost:8474/proxies
# Add latency: curl -X POST http://localhost:8474/proxies/postgres_chaos/toxics \
#   -H "Content-Type: application/json" \
#   -d '{"name":"latency","type":"latency","stream":"upstream","attributes":{"latency":500}}'
```

### 5. Mail Testing

```bash
# Start with visual mail inbox
docker compose -f docker-compose.yml -f compose/compose.mail-visual.yml up -d

# View emails: http://localhost:8025

# Configure NetEngine to relay mail through Mailhog
# In Postfix config: relayhost = mailhog:1025
```

### 6. Multi-World Federation Testing

```bash
# Start two independent worlds
docker compose -f compose/compose.multi-world.yml up -d

# World 1 services on ports:
#   Postgres: localhost:5434
#   Keycloak: localhost:8181

# World 2 services on ports:
#   Postgres: localhost:5435
#   Keycloak: localhost:8182

# Optional: Enable DNS bridge for cross-world lookups
docker compose -f compose/compose.multi-world.yml --profile dns-bridge up -d

# Bootstrap worlds
NETENGINE_DB_URL=postgresql://netengine:world1_pw@localhost:5434/netengine_world1 \
  netengine up examples/minimal.yaml

NETENGINE_DB_URL=postgresql://netengine:world2_pw@localhost:5435/netengine_world2 \
  netengine up examples/minimal.yaml
```

### 7. Security & Audit Logging

```bash
# Start with audit logging enabled
docker compose -f docker-compose.yml -f compose/compose.audit.yml up -d

# Audit dashboard: http://localhost:3001
# View audit logs in Grafana

# Query Postgres audit logs directly:
psql -U netengine -d netengine -c "SELECT * FROM audit.audit_log ORDER BY timestamp DESC LIMIT 10;"
```

## Composing Multiple Overlays

Combine compose files flexibly:

```bash
# Observability + Chaos + Load Testing
docker compose \
  -f docker-compose.yml \
  -f compose/compose.observability.yml \
  -f compose/compose.chaos-network.yml \
  -f compose/compose.load-test.yml \
  up -d

# Boot NetEngine and watch chaos unfold in Grafana
netengine up examples/minimal.yaml
```

## Environment Variables

Create a `.env` file in the compose directory:

```bash
# Database
POSTGRES_PASSWORD=your_secure_password
DB_USER=netengine
DB_PASSWORD=your_db_password
DB_NAME=netengine

# Grafana
GRAFANA_ADMIN_PASSWORD=your_grafana_password

# Keycloak
KEYCLOAK_ADMIN_PASSWORD=your_keycloak_password

# Load testing
K6_VUS=50
K6_DURATION=10m
NETENGINE_TARGET_URL=https://api.platform.internal:8080

# Mock mode (skip real Docker calls)
NETENGINE_MOCK=false
```

## Healthchecks

All services include healthchecks. Monitor status:

```bash
# Check all services
docker compose -f docker-compose.yml -f compose/compose.observability.yml ps

# Detailed health
docker ps --filter "health=starting" --filter "health=unhealthy"

# Logs for a service
docker compose logs postgres --follow
docker compose logs keycloak --follow
docker compose logs k6 --follow
```

## Cleanup

```bash
# Remove containers and volumes (careful!)
docker compose -f docker-compose.yml -f compose/compose.observability.yml down -v

# Partial cleanup (keep volumes)
docker compose -f docker-compose.yml -f compose/compose.observability.yml down

# Inspect volumes before deleting
docker volume ls | grep netengine
```

## Troubleshooting

### "Connection refused" errors

Ensure services are healthy:
```bash
docker compose ps --filter "health=unhealthy"
docker compose logs postgres  # Check startup logs
```

### K6 metrics not appearing in Prometheus

K6 needs to be configured to write to Prometheus RW endpoint. Check:
```bash
docker compose logs k6 | grep prometheus
```

### Toxiproxy not intercepting traffic

Verify applications are connecting to toxiproxy:5432, not postgres:5432:
```bash
curl http://localhost:8474/proxies  # See active proxies
```

### Keycloak slow to start

Keycloak's first startup is slow. Give it 60+ seconds:
```bash
docker compose logs keycloak | grep "started"
```

## Adding New Compose Variants

To add a new compose file:

1. Name it `compose.PURPOSE.yml` (consistent naming)
2. Add comments explaining its role and usage
3. Include healthchecks for all services
4. Use profiles for optional services
5. Document in this README under "Quick Reference"
6. Add example usage pattern above

## Notes

- **Volumes**: Each overlay variant uses prefixed volumes to avoid conflicts
- **Networks**: All services share the default network; custom networks can be added
- **Scaling**: Use `docker compose up -d --scale service=N` to replicate services
- **Profiles**: Services marked with `profiles` only start when explicitly requested with `--profile`
- **State persistence**: State files and volumes persist across `docker compose down` (use `-v` to remove)

---

See `COMPOSE_BRAINSTORM.md` for future compose variants and design ideas.
