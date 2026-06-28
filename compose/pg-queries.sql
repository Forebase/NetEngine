-- PostgreSQL monitoring queries for debugging

-- Active queries
SELECT
  pid,
  usename,
  application_name,
  state,
  query,
  query_start,
  state_change,
  backend_start
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start DESC;

-- Long-running transactions
SELECT
  pid,
  usename,
  xact_start,
  state_change,
  query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND (NOW() - xact_start) > INTERVAL '5 minutes'
ORDER BY xact_start;

-- Query statistics (requires pg_stat_statements extension)
SELECT
  query,
  calls,
  total_time,
  mean_time,
  max_time,
  rows
FROM pg_stat_statements
WHERE mean_time > 100
ORDER BY mean_time DESC
LIMIT 20;

-- Cache hit ratio
SELECT
  sum(heap_blks_read) as heap_read,
  sum(heap_blks_hit) as heap_hit,
  sum(heap_blks_hit) / (sum(heap_blks_hit) + sum(heap_blks_read)) as ratio
FROM pg_statio_user_tables;

-- Table sizes
SELECT
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname != 'pg_catalog'
  AND schemaname != 'information_schema'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 20;

-- Index usage
SELECT
  schemaname,
  tablename,
  indexname,
  idx_scan,
  idx_tup_read,
  idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC
LIMIT 20;

-- Slow queries (if log_min_duration_statement is set)
SELECT
  query,
  calls,
  mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;
