#!/bin/bash

# Apply network limitations using tc (traffic control)
# Simulates slow, high-latency, high-packet-loss networks

set -e

IFACE="${IFACE:-eth0}"
BANDWIDTH="${BANDWIDTH:-1mbit}"   # 1 Mbps
LATENCY="${LATENCY:-100ms}"       # 100ms
LOSS="${LOSS:-5%}"                # 5% packet loss
JITTER="${JITTER:-10ms}"          # 10ms jitter

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

log "Applying network limitations on $IFACE"
log "  Bandwidth: $BANDWIDTH"
log "  Latency: $LATENCY"
log "  Packet loss: $LOSS"
log "  Jitter: $JITTER"

# Find the network interface if not provided
if [[ ! -d "/sys/class/net/$IFACE" ]]; then
  log "Interface $IFACE not found, searching..."
  IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
  log "Found interface: $IFACE"
fi

# Remove any existing qdisc
tc qdisc del dev "$IFACE" root 2>/dev/null || true

# Add root qdisc with HTB (Hierarchical Token Bucket)
tc qdisc add dev "$IFACE" root handle 1: htb default 11

# Add class with bandwidth limit
tc class add dev "$IFACE" parent 1: classid 1:11 htb rate "$BANDWIDTH"

# Add netem (network emulation) qdisc for latency and loss
tc qdisc add dev "$IFACE" parent 1:11 handle 20: netem \
  rate "$BANDWIDTH" \
  delay "$LATENCY" "$JITTER" \
  loss "$LOSS" \
  duplicate 0% \
  reorder 0%

log "Network limitations applied successfully"

# Display current settings
log "Current network qdisc:"
tc qdisc show dev "$IFACE"

log "Keeping limitations active, press Ctrl+C to stop..."

# Keep container running
tail -f /dev/null
