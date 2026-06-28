#!/bin/bash

# Common DNS debugging queries

set -e

DNS_SERVER="${DNS_SERVER:-localhost}"
DOMAIN="${DOMAIN:-platform.internal}"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

log "DNS Debugging Tool"
log "Server: $DNS_SERVER"
log "Domain: $DOMAIN"
echo ""

# Query types to test
query_types=(A AAAA MX NS SOA CNAME TXT)

for qtype in "${query_types[@]}"; do
  log "Querying $qtype records for $DOMAIN..."
  dig @"$DNS_SERVER" "$DOMAIN" "$qtype" +short 2>/dev/null || log "  No $qtype records found"
done

echo ""
log "Full DNS response:"
dig @"$DNS_SERVER" "$DOMAIN" +all

echo ""
log "Zone transfer (AXFR) test:"
dig @"$DNS_SERVER" "$DOMAIN" AXFR 2>/dev/null || log "  Zone transfer denied (expected)"

echo ""
log "Reverse DNS lookup test:"
dig @"$DNS_SERVER" -x 127.0.0.1 +short

echo ""
log "Recursion test:"
dig @"$DNS_SERVER" google.com +short

echo ""
log "DNSSEC validation:"
dig @"$DNS_SERVER" "$DOMAIN" +dnssec +short
