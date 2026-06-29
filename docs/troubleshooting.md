# Troubleshooting

Start with the commands below before changing state:

```bash
poetry run netengine doctor --spec examples/minimal.yaml
poetry run netengine status
poetry run netengine diagnose <spec.yaml>
poetry run netengine events
```

## Phase prerequisites are not satisfied

A previous phase did not complete or its recorded outputs are missing.

```bash
poetry run netengine status
poetry run netengine up <spec.yaml> --phase <phase-number>
```

Do not manually mark phases complete. Fix the underlying dependency and re-run `up` so the orchestrator can write a valid state transition.

## Docker unavailable or stale resources

If Docker is expected to be available, start Docker Desktop or the Docker daemon and rerun `doctor`. To catch Docker subnet conflicts before `up`, run `poetry run netengine doctor --spec <spec.yaml>`; if it reports that an existing Docker network reuses or overlaps a requested world subnet, remove the stale network with `docker network rm <name>` or choose a different subnet in the spec. If you intentionally want no infrastructure side effects, set mock mode:

```bash
NETENGINE_MOCK=true poetry run netengine up examples/minimal.yaml
```

Use dry-run teardown before deleting resources:

```bash
poetry run netengine down --dry-run
```

## Database and migration failures

Inspect migration state:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT version, dirty FROM schema_migrations;"
psql "$NETENGINE_DB_URL" -c "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"
```

If a migration fails:

1. Stop API, worker, and CLI processes that may write to the database.
2. Save the failing migration name/version, application logs, and `schema_migrations` output.
3. Inspect partially-created database objects.
4. Follow the migration file's explicit manual recovery notes, if present.
5. If there are no recovery notes, restore from backup or rebuild only disposable environments.

For disposable local databases only:

```bash
psql "$NETENGINE_DB_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
poetry run python -m netengine.utils.run_migrations
```

## Queue and dead-letter handling

```bash
poetry run netengine events
poetry run netengine events --queue dns_updates --dlq --limit 20
```

Replay a DLQ only after fixing the root cause:

```bash
curl -sk -X POST -H "Authorization: Bearer $NETENGINE_TOKEN" \
  "$NETENGINE_API/api/v1/queues/dns_updates/dlq/replay"
```

Every primary queue should have a matching `*_dlq` queue.

## Reload rejected

`reload` computes a diff and refuses immutable changes before touching live state. Common immutable changes include world identity, schema compatibility boundaries, and destructive persistent-world mutations. Create a new world or use an explicit migration path instead of forcing the reload.

## Corrupt or incompatible runtime state

The local runtime state file is authoritative for resume. If the file is from an unknown future schema, export with the older compatible release or restore from a known-good bundle. For disposable local environments only, remove state and rebuild:

```bash
rm -f netengines_state.json
poetry run netengine up examples/minimal.yaml
```

Never attach raw runtime state to issues; create a redacted support bundle instead.

## API authentication failures

- `Bearer token required`: send `Authorization: Bearer <token>`.
- `Admin role required`: use a token with `admin`, `netengine-admin`, or `operator-admin`.
- `No world is currently running`: boot or import a world before querying world state.
