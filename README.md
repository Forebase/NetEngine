# NetEngine
## Your internet, your control.

NetEngine is a declarative platform for bootstrapping self-contained, authority-autonomous digital worlds. Give it a YAML spec and it provisions authoritative DNS, a private PKI/ACME CA, OIDC identity (Keycloak), network isolation (nftables), domain and world registries, mail, storage, and org applications — all running in Docker on a single host.

---

## What it does

A *world* is a self-contained internet: its own TLD hierarchy, its own certificate authority, its own identity provider, and its own network policies. NetEngine turns a YAML spec into a live world in under ten minutes.

```
netengine up examples/minimal.yaml
```

That single command runs nine phases in sequence:

| Phase | What it provisions |
|---|---|
| 0 — Substrate | Docker networks, NTP, orchestrator init |
| 1–2 — DNS | CoreDNS root + platform zones, TLD hierarchy |
| 3 — PKI | step-ca root CA + ACME endpoint |
| 4 — Platform identity | Keycloak realm, admin user, platform OIDC client |
| 5 — Registries | World registry, domain registry, WHOIS server |
| 6 — In-world identity | Per-org Keycloak realms |
| 7 — ANDs | Administrative Network Domains (nftables isolation) |
| 8 — Services | Postfix, MinIO |
| 9 — Org applications | Org app deployments |

Each phase is idempotent — re-running `netengine up` skips already-completed phases.

---

## Prerequisites

- **Python 3.13+**
- **Docker** (Engine 24+, Compose optional)
- **PostgreSQL 15+** with the [pgmq](https://github.com/tembo-io/pgmq) extension — the easiest way is `docker compose up -d db` using the included `docker-compose.yml`
- **Poetry** (`pip install poetry`)

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/Forebase/NetEngine.git
cd NetEngine

# 2. Install dependencies
poetry install

# 3. Verify host prerequisites
poetry run netengine doctor

# 4. Start local Postgres + pgmq (includes pgmq extension pre-installed)
docker compose up -d postgres

# 5. Apply migrations
poetry run python -m netengine.utils.run_migrations

# 6. Boot a minimal world
poetry run netengine up examples/minimal.yaml
```

If you only want host/container checks before configuring Postgres, run `poetry run netengine doctor --skip-db`. Check status at any time:

```bash
poetry run netengine status
```

Tear down:

```bash
poetry run netengine down
```

---

## Configuration

Worlds are defined in YAML. See `examples/` for reference:

| File | Description |
|---|---|
| `examples/minimal.yaml` | Bare minimum — no orgs, no ANDs, services off |
| `examples/single-org.yaml` | One organisation with residential AND |
| `examples/dev-sandbox.yaml` | Full dev setup with orgs, ANDs, mail, storage |

### Alpha golden paths

These are the official alpha operator paths. Run them after the Quickstart setup to validate the supported lifecycle.

#### Path A — Minimal smoke world

```bash
poetry run netengine up examples/minimal.yaml
poetry run netengine up examples/minimal.yaml
poetry run netengine status
poetry run netengine diagnose examples/minimal.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

The second `up` proves idempotency.

#### Path B — Single-org world

Uses `examples/single-org.yaml` to prove org identity, DNS delegation, AND profile basics, and registry records.

```bash
poetry run netengine up examples/single-org.yaml
poetry run netengine up examples/single-org.yaml
poetry run netengine status
poetry run netengine diagnose examples/single-org.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

#### Path C — Dev sandbox

Uses `examples/dev-sandbox.yaml` as the feature-rich alpha demo. It is more experimental than Paths A/B if some integrations are still stabilizing.

```bash
poetry run netengine up examples/dev-sandbox.yaml
poetry run netengine up examples/dev-sandbox.yaml
poetry run netengine status
poetry run netengine diagnose examples/dev-sandbox.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

#### Acceptance checklist

- Fresh install works
- Boot completes
- Re-running `up` is idempotent
- `status` is accurate
- `diagnose` explains failures
- `reload` rejects immutable changes
- `down --dry-run` lists resources
- `down --yes` leaves no project-owned Docker resources behind

### Spec composition

Large specs can be split across files:

```bash
# Base + environment overlay
poetry run netengine up examples/spec.base.yaml --env dev

# Inline override
poetry run netengine up spec.yaml --set metadata.name=my-world
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `NETENGINE_DB_URL` | `postgresql://netengine:dev_password@localhost:5432/netengine` | Local Postgres connection string |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | — | Set both to use Supabase cloud instead of local Postgres |
| `NETENGINE_STATE_FILE` | `netengines_state.json` | Path to runtime state JSON |
| `NETENGINE_MOCK` | `false` | Set `true` to skip real Docker/DNS/PKI calls (useful for CI) |
| `NETENGINE_ZONE_DIR` | `./data/coredns` | Directory for CoreDNS zone files |

---


## Operator migration guidance

Alpha migrations are forward-only unless a migration file includes explicit manual rollback notes. Inspect applied migrations with:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT version, dirty FROM schema_migrations;"
psql "$NETENGINE_DB_URL" -c "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"
```

The second command identifies the last applied migration. If a migration fails, stop writers, capture the error and current `schema_migrations` state, inspect partially-created objects, and follow that migration's manual recovery notes before retrying. Without explicit rollback notes, restore from backup or rebuild only if the database is disposable. Wiping and reapplying migrations is acceptable for local/dev/CI alpha databases with no durable data; it is unsafe for shared, persistent, staging, production, or customer-like environments without an approved backup/restore plan.

pgmq queue additions should be treated as forward schema changes and should create both the queue and matching `*_dlq` queue. Prefer migrations over manual changes. If a queue must be created manually during alpha recovery, first inspect existing queues and then create both queues explicitly; remove queues only in disposable environments or after confirming no pending/audit messages are needed:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT queue_name FROM pgmq.list_queues() ORDER BY queue_name;"
psql "$NETENGINE_DB_URL" -c "SELECT pgmq.create('new_queue'); SELECT pgmq.create('new_queue_dlq');"
```

See `docs/runbook.md` for the full rollback and recovery procedure.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  netengine CLI  (click)                         │
│    up / down / status / reload / migrate        │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  Orchestrator  (core/orchestrator.py)           │
│    Sequential phase execution, state machine    │
│    Skip logic (idempotent re-runs)              │
└────────────────┬────────────────────────────────┘
                 │
        ┌────────┴─────────┐
        │  Phase handlers   │  phases/ + handlers/
        │  0–9, each with:  │
        │  execute()        │
        │  healthcheck()    │
        │  should_skip()    │
        └────────┬──────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  Event bus  (pgmq over Postgres)                │
│    EventEnvelope with correlation_id            │
│    ConsumerSupervisor for background workers    │
└─────────────────────────────────────────────────┘
```

**Runtime state** is persisted to `netengines_state.json` after each phase so interrupted runs can resume where they left off.

**Events** flow phase-to-phase via pgmq queues defined by `netengine/events/queues.py::PRIMARY_QUEUES`. There are currently 11 primary queues (`dns_updates`, `oidc_provisioning`, `and_provisioning`, `inworld_admissions`, `services_admissions`, `and_admissions`, `pki_cert_rotation_events`, `drift_events`, `world_health`, `gateway_portal_events`, `phase_events`) plus 11 matching dead-letter queues (`*_dlq`) for failed messages.

---

## Development

```bash
# Run tests
poetry run pytest

# Type checking
poetry run mypy netengine

# Linting
poetry run black netengine tests
poetry run isort netengine tests
poetry run flake8 netengine

# Mock-mode test (no Docker needed)
NETENGINE_MOCK=true poetry run netengine up examples/minimal.yaml
```

### Project layout

```
netengine/
  cli/          Click CLI (up, down, status, reload, migrate)
  core/         Orchestrator, state, pgmq client, consumer supervisor
  handlers/     Phase implementation handlers (DNS, PKI, gateway, …)
  phases/       Phase handler wrappers (identity, registries, ANDs, services)
  spec/         Pydantic v2 models + YAML loader with cross-field validation
  events/       EventEnvelope schema (locked)
  api/          FastAPI operator API
  logging/      Structured logging (loguru)
  errors.py     Error hierarchy (SubstrateError, DNSError, PKIError, …)
migrations/     SQL schema + pgmq queue setup
examples/       Reference YAML specs
docs/           Architecture decisions, audit findings
```

---

## Operator API

When a world is running, the operator API is available at `https://api.platform.internal:8080`. Authentication uses the platform OIDC realm — include a bearer token from Keycloak.

```
GET  /health          Liveness check
GET  /world           Current world spec + phase status
GET  /phases/{n}      Individual phase status and output
```

---

## Roadmap to v1

The active development roadmap lives in the [GitHub project](https://github.com/Forebase/NetEngine). Key items:

- [x] End-to-end integration test (real Docker, live DNS query, cert issuance, OIDC login)
- [x] Complete operator API (org CRUD, AND management, domain management)
- [x] Cross-world federation
- [x] `persistent` lifecycle mode (import/export, lifecycle guards, teardown confirmation)
- [x] `netengine down --dry-run`

---

## License

See `LICENSE`.
