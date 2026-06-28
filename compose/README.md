# NetEngine Compose Configurations

A modular collection of Docker Compose files for different workflows, testing scenarios, and operational setups.

## Quick Reference

| File | Purpose | Use Case |
|------|---------|----------|
| `compose.test-minimal.yml` | Postgres only (CI-friendly) | Unit tests, fast CI runs |
| `compose.test-integration.yml` | Full integration: Postgres + pgmq + Keycloak + CoreDNS | End-to-end integration tests |
| `compose.test-network.yml` | Network testing: CoreDNS, nftables, network policies | Network isolation and DNS testing |
| `compose.observability.yml` | Prometheus, Grafana, Loki, Jaeger | Monitoring, debugging, tracing |
| `compose.load-test.yml` | K6 load testing orchestration | Performance testing, capacity planning |
| `compose.chaos-network.yml` | Toxiproxy for failure injection | Resilience testing, chaos engineering |
| `compose.chaos-db.yml` | Database chaos: slowness, connection limits | Database resilience testing |
| `compose.benchmarks.yml` | pgbench, DNS profiler, cert timing | Performance baselines and profiling |
| `compose.mail-visual.yml` | Mailhog + Postfix | Email integration testing |
| `compose.multi-world.yml` | Two independent NetEngine instances | Federation testing, cross-world scenarios |
| `compose.world-bridge.yml` | DNS bridge + network bridging | Cross-world communication testing |
| `compose.keycloak-multi-realm.yml` | Keycloak with platform + org realms | Identity federation testing |
| `compose.oauth-provider-test.yml` | Multiple OIDC providers | Provider federation and switching |
| `compose.storage-multi.yml` | Multiple MinIO + S3-compatible backends | Multi-backend storage testing |
| `compose.state-replay.yml` | State file replay and recovery | State consistency and recovery testing |
| `compose.audit.yml` | Postgres with pgAudit, audit logs | Security auditing, compliance |
| `compose.offline.yml` | Local registries, no external pulls | Air-gapped / offline environments |
| `compose.arm64.yml` | ARM64-native images | ARM64 deployment validation |
| `compose.gpu.yml` | GPU-accelerated services | GPU workload testing and profiling |

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

### 8. Full Integration Testing

```bash
# Start complete integration test environment
docker compose -f docker-compose.yml -f compose/compose.test-integration.yml up -d

# Wait for all services to be healthy
docker compose -f docker-compose.yml -f compose/compose.test-integration.yml ps

# Run integration tests
docker compose -f docker-compose.yml -f compose/compose.test-integration.yml exec test-runner pytest tests/integration/ -v

# Teardown
docker compose -f docker-compose.yml -f compose/compose.test-integration.yml down -v
```

### 9. Network Testing & Policies

```bash
# Start network testing environment
docker compose -f docker-compose.yml -f compose/compose.test-network.yml up -d

# Test DNS resolution
docker compose -f docker-compose.yml -f compose/compose.test-network.yml exec netshoot dig @coredns-network-test world.internal

# Run bandwidth test
docker compose -f docker-compose.yml -f compose/compose.test-network.yml exec iperf3-client iperf3 -c iperf3-server -t 30

# Capture packets
docker compose -f docker-compose.yml -f compose/compose.test-network.yml exec packet-sniffer tcpdump -A -i any port 53
```

### 10. Database Chaos Engineering

```bash
# Start with Toxiproxy intercepting database
docker compose -f docker-compose.yml -f compose/compose.chaos-db.yml up -d

# Apply latency chaos (500ms)
curl -X POST http://localhost:8474/proxies/postgres_chaos/toxics \
  -H "Content-Type: application/json" \
  -d '{"name":"latency","type":"latency","stream":"upstream","attributes":{"latency":500}}'

# Test connection pool behavior
NETENGINE_DB_URL=postgresql://netengine:chaos_test_password@localhost:5439/netengine_chaos \
  poetry run python -m pytest tests/chaos/

# View Toxiproxy dashboard
curl http://localhost:8474/proxies
```

### 11. Performance Benchmarking

```bash
# Start benchmark environment
docker compose -f docker-compose.yml -f compose/compose.benchmarks.yml up -d

# Run pgbench (wait for setup)
docker compose -f docker-compose.yml -f compose/compose.benchmarks.yml exec pgbench-runner \
  tail -f /tmp/bench-mixed-result.txt

# View results
docker compose -f docker-compose.yml -f compose/compose.benchmarks.yml exec pgbench-runner \
  cat /tmp/bench-*-result.txt
```

### 12. Multi-Realm Identity Testing

```bash
# Start Keycloak with multiple realms
docker compose -f docker-compose.yml -f compose/compose.keycloak-multi-realm.yml up -d

# Access Keycloak admin
# Primary: http://localhost:8184/admin (admin/keycloak_test_password)
# Replica: http://localhost:8185/admin

# Test realm switching
curl -X POST http://keycloak-primary:8080/realms/org1/protocol/openid-connect/token \
  -d "grant_type=password" -d "client_id=test" -d "username=user1" -d "password=test_password_123"
```

### 13. Cross-World Federation

```bash
# Start federation relay and bridge
docker compose -f compose/compose.multi-world.yml -f compose/compose.world-bridge.yml up -d

# Check bridge health
curl http://localhost:7777/bridge/health

# List federated services
curl http://localhost:7777/services
```

### 14. Storage Multi-Backend Testing

```bash
# Start with multiple storage backends
docker compose -f docker-compose.yml -f compose/compose.storage-multi.yml up -d

# Access MinIO primary console
# http://localhost:9001 (minioadmin/minioadmin_password_123)

# Test multi-backend operations
docker compose -f docker-compose.yml -f compose/compose.storage-multi.yml exec storage-test-client \
  python3 << 'EOF'
import boto3

# Test each backend
backends = {
    "primary": ("http://minio-primary:9000", "minioadmin", "minioadmin_password_123"),
    "secondary": ("http://minio-secondary:9000", "minioadmin", "minioadmin_password_123"),
}

for name, (endpoint, ak, sk) in backends.items():
    s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=ak, aws_secret_access_key=sk)
    s3.create_bucket(Bucket=f"test-{name}")
    print(f"✓ {name}: bucket created")
EOF
```

### 15. Offline/Air-Gapped Deployment

```bash
# Start offline environment (pre-cached images)
docker compose -f docker-compose.yml -f compose/compose.offline.yml up -d

# Verify local registry
curl http://localhost:5000/v2/

# Validate offline environment
docker compose -f docker-compose.yml -f compose/compose.offline.yml logs offline-validator

# Deploy without external internet access
NETENGINE_MOCK=true poetry run netengine up examples/minimal.yaml
```

### 16. ARM64 Testing

```bash
# Start ARM64 validation environment
docker compose -f docker-compose.yml -f compose/compose.arm64.yml up -d

# Check ARM64 compatibility
docker compose -f docker-compose.yml -f compose/compose.arm64.yml exec arm64-compat-check python3 -c "
import platform
print(f'Architecture: {platform.machine()}')
print(f'System: {platform.system()}')"

# Run ARM64 benchmarks
docker compose -f docker-compose.yml -f compose/compose.arm64.yml logs arm64-benchmark
```

### 17. GPU-Accelerated Workloads

```bash
# Start GPU services (requires nvidia-docker)
docker compose -f docker-compose.yml -f compose/compose.gpu.yml up -d

# Verify GPU access
docker compose -f docker-compose.yml -f compose/compose.gpu.yml exec gpu-detector nvidia-smi

# Monitor GPU
docker compose -f docker-compose.yml -f compose/compose.gpu.yml logs -f gpu-monitor

# Run GPU benchmark
docker compose -f docker-compose.yml -f compose/compose.gpu.yml logs gpu-benchmark
```

### 18. State Recovery & Replay

```bash
# Start state replay environment
docker compose -f docker-compose.yml -f compose/compose.state-replay.yml up -d

# Copy state files for replay
docker compose -f docker-compose.yml -f compose/compose.state-replay.yml exec state-replayer \
  ls -la /data/states/

# Validate state consistency
docker compose -f docker-compose.yml -f compose/compose.state-replay.yml exec state-validator \
  python3 /app/validate.py
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

See `docs/compose-brainstorm.md` for future compose variants and design ideas.
