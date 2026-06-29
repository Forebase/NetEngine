# Alpha quickstart

This guide is the focused alpha operator path. The root `README.md` keeps the high-level overview; use this file when you need exact commands for a clean local bootstrap, smoke validation, and teardown.

## Prerequisites

- Python 3.13+
- Docker Engine 24+ with the Docker socket available to the current user
- Poetry
- PostgreSQL 15+ with the `pgmq` extension
- `psql`, `curl`, and `jq` for diagnostics

## Clean checkout bootstrap

```bash
git clone https://github.com/Forebase/NetEngine.git
cd NetEngine
poetry install
poetry run netengine setup local examples/minimal.yaml
poetry run netengine status
```

Use `poetry run netengine setup local examples/minimal.yaml` for the guided first-time path: it runs host checks that are safe before Postgres starts, starts compose services, waits for database health, applies migrations, performs spec-aware doctor checks, and stops before `netengine up` if required checks fail. Use `NETENGINE_MOCK=true` when you want to exercise orchestration and spec validation without creating Docker, DNS, PKI, or identity resources.

## Pre-release mock smoke test

Before cutting an alpha build, run the documented mock bootstrap path with an isolated runtime-state file:

```bash
NETENGINE_MOCK=true NETENGINE_STATE_FILE="$(mktemp -d)/netengine_state.json" poetry run netengine up examples/minimal.yaml
```

The command should print `World bootstrapped.` and report all phases through Phase 9 as complete without creating Docker, DNS, PKI, or identity resources.

## Alpha golden paths

### Path A: minimal smoke world

```bash
poetry run netengine up examples/minimal.yaml
poetry run netengine up examples/minimal.yaml
poetry run netengine status
poetry run netengine diagnose examples/minimal.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

The second `up` proves idempotency.

### Path B: single-org world

```bash
poetry run netengine up examples/single-org.yaml
poetry run netengine up examples/single-org.yaml
poetry run netengine status
poetry run netengine diagnose examples/single-org.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

This path validates org identity, DNS delegation, basic AND profile wiring, and registry records.

### Path C: dev sandbox

```bash
poetry run netengine up examples/dev-sandbox.yaml
poetry run netengine up examples/dev-sandbox.yaml
poetry run netengine status
poetry run netengine diagnose examples/dev-sandbox.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

The sandbox is the feature-rich demo and may expose experimental alpha integrations before Paths A and B do.

## Acceptance checklist

- Fresh install works.
- Boot completes.
- Re-running `up` is idempotent.
- `status` reports the expected world and phases.
- `diagnose` explains failures without requiring log archaeology.
- `reload` rejects immutable changes.
- `down --dry-run` lists project-owned resources.
- `down --yes` removes non-persistent project-owned Docker resources.

## Useful environment variables

| Variable | Default | Use |
|---|---|---|
| `NETENGINE_DB_URL` | `postgresql://netengine:dev_password@localhost:5432/netengine` | Runtime database and pgmq connection. |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | unset | Use Supabase-hosted persistence instead of local Postgres. |
| `NETENGINE_STATE_FILE` | `netengines_state.json` | Runtime-state JSON file path. |
| `NETENGINE_MOCK` | `false` | Skip real Docker/DNS/PKI side effects. |
| `NETENGINE_ZONE_DIR` | `./data/coredns` | CoreDNS zone-file directory. |

## Next documents

- `docs/operator-guide.md` for day-2 operations.
- `docs/troubleshooting.md` for failure recovery.
- `docs/security-model.md` for secrets and trust boundaries.
- `docs/support-matrix.md` for alpha feature support.
