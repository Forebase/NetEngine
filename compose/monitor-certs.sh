#!/bin/bash

# Monitor certificate expiration and alert

CERT_DIR="/certs"
ALERT_DAYS=30

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

check_cert() {
  local cert_file="$1"
  local cert_name=$(basename "$cert_file" .crt)

  if [[ ! -f "$cert_file" ]]; then
    log "ERROR: Certificate not found: $cert_file"
    return 1
  fi

  # Get expiration date
  EXPIRY=$(openssl x509 -in "$cert_file" -noout -enddate | cut -d= -f2)
  EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s)
  NOW_EPOCH=$(date +%s)
  DAYS_LEFT=$(( ($EXPIRY_EPOCH - $NOW_EPOCH) / 86400 ))

  log "Certificate: $cert_name"
  log "  Expires: $EXPIRY"
  log "  Days remaining: $DAYS_LEFT"

  if [[ $DAYS_LEFT -lt 0 ]]; then
    log "  WARNING: Certificate has EXPIRED!"
  elif [[ $DAYS_LEFT -lt $ALERT_DAYS ]]; then
    log "  WARNING: Certificate expires in $DAYS_LEFT days (threshold: $ALERT_DAYS days)"
  else
    log "  OK: Certificate is valid"
  fi

  echo ""
}

log "Certificate Monitoring Service"
log "Alert threshold: $ALERT_DAYS days"
echo ""

# Initial check
for cert in "$CERT_DIR"/*.crt; do
  check_cert "$cert"
done

# Periodic monitoring
while true; do
  sleep 86400  # Check once daily
  log "Running daily certificate check..."
  for cert in "$CERT_DIR"/*.crt; do
    check_cert "$cert"
  done
done
