# Operator guide

This guide covers common day-2 operations for an alpha NetEngine world.

## Lifecycle commands

```bash
poetry run netengine doctor
poetry run netengine up examples/minimal.yaml
poetry run netengine status
poetry run netengine diagnose examples/minimal.yaml
poetry run netengine reload examples/minimal.yaml
poetry run netengine down --dry-run
poetry run netengine down --yes
```

Persistent worlds require typed confirmation for destructive teardown:

```bash
poetry run netengine down --confirm <world-name>
```

`--yes` alone is intentionally insufficient for persistent destructive operations.

## Phase model

`netengine up` runs ten numbered phases and persists runtime state after each phase:

| Phase | Area | Operator outcome |
|---:|---|---|
| 0 | Substrate | Docker networks, NTP checks, orchestrator initialization. |
| 1-2 | DNS | CoreDNS root, platform zones, and TLD hierarchy. |
| 3 | PKI | step-ca root, optional intermediate metadata, and ACME endpoint. |
| 4 | Platform identity | Keycloak platform realm, bootstrap admin, and platform OIDC client. |
| 5 | Registries | World registry, domain registry, WHOIS, registrar surfaces. |
| 6 | In-world identity | Per-org/in-world identity realms and users. |
| 7 | ANDs | Administrative Network Domain network isolation. |
| 8 | Services | Mail and storage services where enabled. |
| 9 | Org apps | Org application deployments from the catalog. |

Re-running `up` skips completed phases when their state is valid.

## Runtime inspection

```bash
poetry run netengine status
poetry run netengine events
poetry run netengine events --queue dns_updates --dlq --limit 20
```

Use `events` to inspect pgmq queue depths and dead-letter messages before replaying work.

## Operator API

Start the API locally with:

```bash
poetry run netengine serve
```

The alpha compatibility boundary is `/api/v1`. Interactive OpenAPI documentation is available at `/docs`, and the raw schema is at `/openapi.json`.

```bash
export NETENGINE_TOKEN='<platform-oidc-access-token>'
export NETENGINE_API='https://api.platform.internal:8080'

curl -sk -H "Authorization: Bearer $NETENGINE_TOKEN" "$NETENGINE_API/api/v1/world"
curl -sk -H "Authorization: Bearer $NETENGINE_TOKEN" "$NETENGINE_API/api/v1/queues"
curl -sk -H "Authorization: Bearer $NETENGINE_TOKEN" "$NETENGINE_API/api/v1/identity/realms"
```

State-changing routes require an admin role. Accepted alpha admin role names are `admin`, `netengine-admin`, and `operator-admin`.

## Admin API examples

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

## Import and export

```bash
poetry run netengine export --out netengine-support-bundle.json
poetry run netengine import netengine-support-bundle.json
```

Support bundles redact secrets by default and are intended for support and compatible restores, not for secret escrow.

## Migration operations

```bash
poetry run python -m netengine.utils.run_migrations
psql "$NETENGINE_DB_URL" -c "SELECT version, dirty FROM schema_migrations;"
psql "$NETENGINE_DB_URL" -c "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"
```

Alpha migrations are forward-only unless a migration file includes explicit manual rollback notes.
