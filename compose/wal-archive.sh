#!/bin/bash

# Archive PostgreSQL WAL segments to S3-compatible storage (MinIO)

set -e

# Configuration
ARCHIVE_TIMEOUT=300
MAX_RETRIES=3
RETRY_DELAY=5

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

log "WAL Archival Service Starting"

# Wait for Postgres to be ready
log "Waiting for PostgreSQL to be ready..."
until psql -U "$PGUSER" -d "$PGDATABASE" -h "$PGHOST" -c "SELECT 1" > /dev/null 2>&1; do
  sleep 2
done

log "PostgreSQL is ready"

# Wait for MinIO to be ready
log "Waiting for MinIO to be ready..."
until curl -s http://minio:9000/minio/health/live > /dev/null 2>&1; do
  sleep 2
done

log "MinIO is ready"

# Configure PostgreSQL archive settings
log "Configuring PostgreSQL WAL archival..."

psql -U "$PGUSER" -d "$PGDATABASE" -h "$PGHOST" << EOF
ALTER SYSTEM SET archive_mode = ON;
ALTER SYSTEM SET archive_command = 'aws s3 cp %p s3://netengine-backups/wal/%f --endpoint-url http://minio:9000 --no-progress';
ALTER SYSTEM SET archive_timeout = $ARCHIVE_TIMEOUT;
EOF

log "PostgreSQL archive settings updated"
log "WAL archival is active and monitoring"

# Monitor archival status
while true; do
  ARCHIVED=$(psql -U "$PGUSER" -d "$PGDATABASE" -h "$PGHOST" -t -c "SELECT count(*) FROM pg_stat_archiver;")
  log "Archived segments: $ARCHIVED"
  sleep 60
done
