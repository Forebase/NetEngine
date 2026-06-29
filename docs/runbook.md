# NetEngine Local Development Runbook

Getting from a clean checkout to a running local environment, plus common troubleshooting procedures.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.13+ | [python.org](https://python.org) or `pyenv install 3.13` |
| Poetry | latest | `pip install poetry` |
| Docker + Compose | 24+ | [docker.com](https://docker.com) |
| `psql` | any | `brew install postgresql` / `apt install postgresql-client` |

---

## Quick start (mock mode)

Mock mode simulates all infrastructure without Docker, databases, or DNS.
Use it to iterate on spec changes and business logic:

```bash
git clone https://github.com/Forebase/NetEngine
cd NetEngine
poetry install

# Run all tests
make test

# Bootstrap a world in mock mode (no real infra created)
NETENGINE_MOCK=1 poetry run netengine up examples/minimal.yaml
```

That's it — all 10 phases run end-to-end and runtime state is written to
`~/.netengine/state.json`.

---

## Full local stack (phases 0–4)

This brings up the real persistence and identity layers.

### 1. Guided setup and bootstrap

```bash
export NETENGINE_DB_URL="postgresql://netengine:dev_password@localhost:5432/netengine"
poetry run netengine setup local examples/minimal.yaml
```

The guided setup replaces the former manual sequence of `doctor`, `docker compose up`, database waiting, migrations, another doctor run, and `netengine up`. It runs pre-Postgres host checks first, starts the required compose services, waits for `netengine_postgres` to become healthy, applies migrations, runs spec-aware doctor/database checks, and stops before bootstrapping if required checks fail.

The setup workflow provides remediation hints for common blockers:
- **Subnet conflicts:** remove conflicting Docker networks with `docker network rm <name>` or choose non-overlapping subnets in the world spec.
- **Port conflicts:** stop the process/container using the published port or change the compose/spec port.
- **Docker name conflicts:** run `netengine down`, `docker compose down`, or remove stale containers before retrying.
- **Database failures:** inspect `docker compose ps postgres` and `docker logs netengine_postgres`, verify `NETENGINE_DB_URL`, then rerun `netengine setup local examples/minimal.yaml`.

This runs all phases sequentially. Each phase reports `completed successfully`
when done. Runtime state is saved after each phase; the run is resumable if
interrupted.

To stop at a specific phase (e.g. stop after Phase 4):

```bash
poetry run netengine up examples/minimal.yaml --phase 4
```

### 4. Verify

```bash
# Check runtime state
poetry run netengine status

# Inspect event queue depths
poetry run netengine events

# Start the operator API
poetry run netengine serve

# Health endpoint
curl http://localhost:8000/health
```

---

## Development workflow

### Running the test suite

```bash
make test                            # unit tests only
poetry run pytest tests/integration  # integration tests (mock mode, no DB needed)
poetry run pytest tests/ -k "reload" # filter by name
```

### Lint and type checks

```bash
make lint         # mypy + black + isort + flake8 (check only)
make format       # auto-format with black + isort
```

Or individually:

```bash
poetry run mypy netengine --strict
poetry run black netengine tests
poetry run isort netengine tests
poetry run flake8 netengine tests
```

### Applying a spec change without restarting

Edit your spec YAML, then:

```bash
poetry run netengine reload path/to/spec.yaml
```

The reload engine computes the diff, checks immutable fields, and applies
only the changed sections. Immutability violations are reported without
touching any live state.

### Inspecting event queues

NetEngine's pgmq queue inventory is defined in
`netengine/events/queues.py::PRIMARY_QUEUES`. There are currently 11 primary
queues, and each primary queue has one matching dead-letter queue (`*_dlq`) for
failed messages.

```bash
# Show all queue depths
poetry run netengine events

# Show dead-letter queue contents for one queue
poetry run netengine events --queue dns_updates --dlq --limit 20
```

---

## Troubleshooting

### `RuntimeError: Phase N prerequisite(s) not satisfied`

A previous phase did not complete. Run `netengine status` to see which phases
are marked complete, then re-run starting from the failing phase:

```bash
poetry run netengine up examples/minimal.yaml --phase 3  # re-run up to phase 3
```

### `Docker unavailable, falling back to mock mode`

Docker socket is not reachable. Start Docker Desktop or the Docker daemon,
then retry. If you intentionally want mock mode, set `NETENGINE_MOCK=1`.

### State corruption / want a clean slate

```bash
# Remove local runtime state (does NOT touch the database)
rm ~/.netengine/state.json

# Wipe the database and reapply migrations
psql $NETENGINE_DB_URL -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql $NETENGINE_DB_URL -f migrations/001_initial.sql
```

Then re-run `netengine up`.


### Migration rollback and recovery (alpha)

Alpha database migrations are **forward-only** unless the specific migration file includes explicit manual rollback notes. Do not assume a migration can be automatically reversed, and do not delete rows from `schema_migrations` as a rollback mechanism unless a migration's notes specifically instruct you to do so.

Use `psql` to inspect applied migrations:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT version, dirty FROM schema_migrations;"
```

The last applied migration is the highest recorded migration version. If your local migration runner stores timestamps or filenames instead of integer versions, order by the recorded version/name exactly as stored by the runner:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"
```

If a migration fails:

1. Stop NetEngine processes that may write to the database.
2. Capture the failing migration version, `psql` output, and relevant application logs.
3. Inspect `schema_migrations` and the partially-created database objects before retrying.
4. If the database is marked dirty, fix the underlying SQL/object state first; then follow the migration's explicit manual recovery notes before re-running migrations.
5. If there are no rollback/recovery notes, restore from a known-good backup or rebuild an expendable environment rather than hand-editing production data.

Wiping and reapplying migrations is acceptable only for disposable local/dev databases, CI databases, or alpha environments where you have confirmed that no durable world/operator data needs to be preserved. It is unsafe for shared, persistent, staging, production, or customer-like environments unless you have an approved backup/restore plan and explicit operator sign-off.

For local-only rebuilds, a full schema wipe is the cleanest reset:

```bash
psql "$NETENGINE_DB_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
poetry run python -m netengine.utils.run_migrations
```

#### pgmq queue migration notes

Queue additions are schema changes. Prefer adding new pgmq queues in a forward migration with both the primary queue and its dead-letter queue, and keep the migration queue list aligned with `netengine.events.queues.Queue`. Before adding a queue manually, verify whether the migration already created it:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT queue_name FROM pgmq.list_queues() ORDER BY queue_name;"
```

If an alpha migration fails because a pgmq queue is missing and the environment is otherwise recoverable, manually creating the queue can be an acceptable forward fix:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT pgmq.create('new_queue'); SELECT pgmq.create('new_queue_dlq');"
```

Manual removal is riskier: pgmq queue tables can contain pending, delayed, or archived operational messages. Only drop/remove queues in disposable environments, or after confirming the queue is unused and draining/archiving any messages required for audit or replay. Never remove a queue just to make `schema_migrations` look rolled back.

### `SpecLoadError: Spec validation failed`

The YAML spec has a field that failed Pydantic validation. The error message
includes the field path and constraint. Compare against `examples/minimal.yaml`
for a known-good reference.

### Immutability violation on reload

Fields like `substrate.networks`, `dns.listen_ip`, and `pki.listen_ip` are
immutable after bootstrap — changing them requires a full teardown and
re-bootstrap. The error message identifies the exact field and explains why.

To change these fields: run `netengine down`, update the spec, then
`netengine up` from scratch.

### Prometheus metrics not appearing

The `/metrics` endpoint is served by the operator API. Start it with
`netengine serve`, then:

```bash
curl http://localhost:8000/metrics
```

If the API is running but metrics are empty, no phases have been executed yet
in this process lifetime. Phase metrics are in-process only — they reset when
the server restarts.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NETENGINE_MOCK` | `""` | Set to `1`/`true`/`yes` to enable mock mode |
| `NETENGINE_DB_URL` | `""` | asyncpg connection string for pgmq and state sync |
| `NETENGINE_STATE_FILE` | `~/.netengine/state.json` | Override runtime state path |
| `NETENGINE_LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## File locations

| Path | Description |
|------|-------------|
| `~/.netengine/state.json` | Runtime state (phase completion, outputs) |
| `examples/` | Reference spec files |
| `migrations/001_initial.sql` | Database schema + pgmq setup |
| `docs/SUPABASE_SETUP.md` | Cloud database setup guide |
| `docs/decisions.md` | Architecture decision log |
