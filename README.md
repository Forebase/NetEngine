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

See [`docs/alpha-quickstart.md`](docs/alpha-quickstart.md) for alpha golden paths, acceptance checks, and troubleshooting-oriented setup details.

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

## Alpha docs

Operational alpha details live in focused docs so this README stays readable:

| Document | Purpose |
|---|---|
| [`docs/alpha-quickstart.md`](docs/alpha-quickstart.md) | Clean install, golden paths, and acceptance checklist |
| [`docs/support-matrix.md`](docs/support-matrix.md) | Supported, experimental, reserved, and unsupported spec fields |
| [`docs/spec-reference.md`](docs/spec-reference.md) | YAML spec sections, composition, and validation guidance |
| [`docs/operator-guide.md`](docs/operator-guide.md) | Lifecycle commands, phase operations, API examples, migrations |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Failure diagnosis and recovery procedures |
| [`docs/security-model.md`](docs/security-model.md) | Trust boundaries, secrets, redaction, destructive-action safety |
| [`docs/networking.md`](docs/networking.md) | Platform/core networks, DNS layout, ANDs, gateway portal |
| [`docs/state-and-backups.md`](docs/state-and-backups.md) | Runtime state, database state, bundles, backup/restore |
| [`docs/release-notes/0.1.0-alpha.1.md`](docs/release-notes/0.1.0-alpha.1.md) | Initial alpha release notes |
| [`docs/known-limitations.md`](docs/known-limitations.md) | Current alpha limitations and caveats |

---

## Alpha operations and security

Deeper operational guidance has moved into the alpha docs:

- [`docs/operator-guide.md`](docs/operator-guide.md) for lifecycle commands, operator API examples, imports/exports, and migrations.
- [`docs/security-model.md`](docs/security-model.md) for secrets handling, redaction, and destructive-action safeguards.
- [`docs/state-and-backups.md`](docs/state-and-backups.md) for runtime state, support bundles, and backup/restore guidance.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) for common failures and recovery procedures.

---

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

**Runtime state** is persisted to `netengines_state.json` after each phase so interrupted runs can resume where they left off. The state file carries `schema_version: netengine.runtime_state.v1`; pre-version alpha.1 state files are detected as v1-compatible and stamped on the next save, while unknown future/foreign versions fail closed with instructions to export using the older release or migrate through a compatible release. The file is written atomically with `0600` permissions because it can contain runtime secrets (bootstrap admin password, OIDC client secrets, generated in-world admin passwords, and other phase outputs). Do not commit it, attach it to issues, or copy it into shared logs; use a sanitized support bundle instead.

**Spec compatibility** is tracked in `metadata.schema_version` (default `netengine.spec.v1`). The loader accepts missing schema versions for existing alpha specs, rejects unsupported versions before boot/import, and includes the spec schema in support bundles so alpha.2+ can decide whether an alpha.1 world is safe to restore.

**Support bundles** are produced with `netengine export --out netengine-support-bundle.json` or `GET /api/v1/export`. Bundles include schema metadata, the world spec, phase completion, public CA material, and sanitized phase outputs with secret-looking fields/private PEMs removed. Redaction is performed through the `redactable` package when it is installed, with NetEngine's built-in fallback used only for alpha compatibility. Restore with `netengine import <bundle-file>` or `POST /api/v1/import`; import validates the bundle schema, spec schema compatibility, spec parseability, known phases, phase prerequisites, and required outputs before replacing local runtime state.

**Persistent teardown safety** requires typed confirmation. CLI teardown of a persistent world must pass `netengine down --confirm <world-name>`; the operator API requires both `confirm=true` and `confirmation` equal to the world name. `--yes` is intentionally not enough for persistent destructive operations.

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

When a world is running, the operator API is available at `https://api.platform.internal:8080`. Authentication uses the platform OIDC realm — include a bearer token from Keycloak. Mutating routes (`POST`, `PUT`, `PATCH`, and `DELETE`) require an administrative role (`admin`, `netengine-admin`, or `operator-admin`) after the platform realm is available. Token validation errors distinguish missing tokens, inactive/expired tokens, Keycloak introspection failures, and OIDC issuer transport/TLS failures.

During alpha bootstrap (before Phase 4 brings up Keycloak), the operator API is not unauthenticated: callers must send the host-local `X-Bootstrap-Secret` matching `NETENGINES_BOOTSTRAP_SECRET`. The secret is generated/read locally by the CLI, cannot be set over the API, is retired for operator API calls once Phase 4 completes, and must not be committed or included in support artifacts.

TLS verification for OIDC issuer calls defaults to secure certificate validation. For self-signed world CAs, configure `NETENGINE_CA_BUNDLE` or place the runtime CA bundle at `runtime/ca-bundle.pem`. Disabling verification requires explicit opt-in with `NETENGINE_INSECURE_TLS=true` and emits a warning; use it only in isolated development environments.

Unauthenticated health checks intentionally return only phase completion, overall status, and whether an error exists. Detailed runtime errors and state remain behind authenticated operator endpoints.

```bash
curl -sk -X POST -H "Authorization: Bearer $NETENGINE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d @<(jq -n --rawfile spec examples/dev-sandbox.yaml '{spec_yaml: $spec}') \
  "$NETENGINE_API/api/v1/reload"

curl -sk -X POST -H "Authorization: Bearer $NETENGINE_TOKEN" \
  "$NETENGINE_API/api/v1/queues/dns_updates/dlq/replay"

curl -sk -X DELETE -H "Authorization: Bearer $NETENGINE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"confirm": true, "confirmation": "dev-sandbox"}' \
  "$NETENGINE_API/api/v1/world"
```

### Error responses

FastAPI errors use a JSON `detail` field. Examples:

```json
{ "detail": "Bearer token required" }
```

```json
{ "detail": "Admin role required" }
```

```json
{ "detail": "No world is currently running — use netengines up first" }
```

```json
{
  "detail": {
    "success": false,
    "applied": [],
    "rejected": [],
    "errors": ["persistent worlds cannot remove orgs during reload"],
    "immutability_violations": ["metadata.name is immutable"]
  }
}
```

### API versioning and compatibility

`/api/v1` is the alpha compatibility boundary. For alpha releases, existing `/api/v1` paths, request fields, response fields, and documented status semantics should remain backward compatible. Additive fields and additive endpoints may appear without a version bump. Breaking changes require a new prefix such as `/api/v2` or a documented alpha migration note before removal. Bug fixes may make validation stricter when the previous behavior could mutate infrastructure unsafely or return corrupt state.

See [`docs/operator-guide.md`](docs/operator-guide.md) for authentication, route coverage, curl examples, import/export, queue operations, and teardown confirmation details.

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
