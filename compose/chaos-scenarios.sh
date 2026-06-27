#!/bin/sh

# Chaos engineering scenarios for NetEngine
# Run inside chaos-control container with Toxiproxy

TOXIPROXY_URL="http://toxiproxy:8474"
SLEEP_BETWEEN=5

echo "[*] Waiting for Toxiproxy to start..."
sleep 10

# Scenario 1: Add latency to Postgres
echo "[*] Scenario 1: Adding 200ms latency to Postgres..."
curl -X POST "$TOXIPROXY_URL/proxies/postgres_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "postgres_latency",
    "type": "latency",
    "stream": "upstream",
    "attributes": {"latency": 200}
  }'
sleep $SLEEP_BETWEEN

# Scenario 2: Add jitter
echo "[*] Scenario 2: Adding jitter to Postgres..."
curl -X POST "$TOXIPROXY_URL/proxies/postgres_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "postgres_jitter",
    "type": "jitter",
    "stream": "upstream",
    "attributes": {"jitter": 50}
  }'
sleep $SLEEP_BETWEEN

# Scenario 3: Add bandwidth limit
echo "[*] Scenario 3: Adding 1MB/s bandwidth limit to Postgres..."
curl -X POST "$TOXIPROXY_URL/proxies/postgres_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "postgres_bandwidth",
    "type": "bandwidth",
    "stream": "upstream",
    "attributes": {"rate": 1048576}
  }'
sleep $SLEEP_BETWEEN

# Scenario 4: Partial packet loss
echo "[*] Scenario 4: Adding 5% packet loss to Postgres..."
curl -X POST "$TOXIPROXY_URL/proxies/postgres_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "postgres_loss",
    "type": "loss",
    "stream": "upstream",
    "attributes": {"percentage": 5}
  }'
sleep $SLEEP_BETWEEN

# Scenario 5: Connection reset
echo "[*] Scenario 5: Resetting Postgres connections..."
curl -X POST "$TOXIPROXY_URL/proxies/postgres_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "postgres_reset",
    "type": "reset_peer",
    "stream": "both",
    "attributes": {"timeout": 30000}
  }'
sleep $SLEEP_BETWEEN

# Scenario 6: Keycloak latency
echo "[*] Scenario 6: Adding 500ms latency to Keycloak..."
curl -X POST "$TOXIPROXY_URL/proxies/keycloak_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "keycloak_latency",
    "type": "latency",
    "stream": "upstream",
    "attributes": {"latency": 500}
  }'
sleep $SLEEP_BETWEEN

# Scenario 7: Temporary timeout
echo "[*] Scenario 7: Adding timeout to Keycloak..."
curl -X POST "$TOXIPROXY_URL/proxies/keycloak_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "keycloak_timeout",
    "type": "timeout",
    "stream": "upstream",
    "attributes": {"timeout": 5000}
  }'
sleep $SLEEP_BETWEEN

# Scenario 8: Slow close
echo "[*] Scenario 8: Adding slow close to Keycloak..."
curl -X POST "$TOXIPROXY_URL/proxies/keycloak_chaos/toxics" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "keycloak_slowclose",
    "type": "slow_close",
    "stream": "upstream",
    "attributes": {"delay": 10000}
  }'
sleep $SLEEP_BETWEEN

echo "[*] All chaos scenarios applied. Running indefinitely..."
tail -f /dev/null
