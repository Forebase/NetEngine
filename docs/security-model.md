# Security model

NetEngine alpha creates a self-contained administrative world with its own DNS, private PKI, OIDC identity, registries, and network isolation. The default posture is conservative: generated runtime material is sensitive, support artifacts are redacted by default, and destructive operations require explicit operator intent.

## Trust boundaries

| Boundary | Alpha stance |
|---|---|
| Host | The Docker host and local filesystem are trusted operator-controlled infrastructure. |
| Runtime state | Authoritative resume snapshot; may contain generated credentials and should be protected like a secret. |
| Database | Stores operational state, queues, and audit/convenience mirrors; protect it as administrative infrastructure. |
| Operator API | Requires platform OIDC tokens for inspection and admin roles for mutation. |
| In-world services | Isolated by configured networks and AND policy; do not treat alpha isolation as a multi-tenant security certification. |
| Support bundles | Redacted by default, but may still reveal topology and operational metadata. |

## Secrets handling

- Bootstrap and generated admin credentials are created during identity/bootstrap phases or supplied by the deployment environment.
- Specs name admin users but should not store generated passwords.
- Runtime state may contain bootstrap admin passwords, OIDC client secrets, generated in-world passwords, and phase outputs.
- The default runtime state file is written atomically with mode `0600`.
- `netengine export` and `GET /api/v1/export` redact secret-looking fields by default.

## Safe to commit

- Example specs with placeholders.
- Migrations, tests, and documentation.
- Public CA certificates when intentionally published as trust anchors.

## Never commit

- `netengines_state.json` or alternate runtime state files.
- `.env` files with credentials.
- Private keys, generated private certificates, tokens, passwords, database dumps, or raw support bundles.
- Real operator/customer topology when it is sensitive.

## Credential rotation

During alpha, rotate credentials at the backing service, update runtime state or environment values as needed, and restart affected API/worker processes. A dedicated secrets rotation command is planned.

## Redaction model

Redaction is centralized in `netengine.security.redaction`. It uses Sober-Co `redactable` when installed and falls back to local alpha rules. API world responses redact secret fields unless an authenticated admin explicitly requests secrets. Support bundles redact by default.

## Destructive action safety

Persistent teardown requires typed confirmation. The CLI requires `netengine down --confirm <world-name>`, and the operator API requires `confirm=true` plus `confirmation` equal to the world name.
