# State and backups

NetEngine alpha has two important persistence surfaces: local runtime state and the database. Treat both as operationally sensitive.

## Runtime state

The runtime state file is written after each phase so interrupted runs can resume. The default path is `netengines_state.json`, configurable with `NETENGINE_STATE_FILE`.

Runtime state includes phase completion, outputs needed by later phases, schema metadata, and potentially generated secrets. Do not commit it or attach it to support tickets.

## Database state

Postgres with `pgmq` stores runtime persistence, event queues, and migration state. Inspect migrations with:

```bash
psql "$NETENGINE_DB_URL" -c "SELECT version, dirty FROM schema_migrations;"
```

Queue state can be inspected with:

```bash
poetry run netengine events
```

## Support bundles

Create a redacted support bundle:

```bash
poetry run netengine export --out netengine-support-bundle.json
```

Import a compatible bundle:

```bash
poetry run netengine import netengine-support-bundle.json
```

Bundles include schema metadata, the spec, phase completion, public CA material, and sanitized phase outputs. They are intended for support and compatible restores, not for long-term secret escrow.

## Backup recommendations

For alpha environments with durable data:

1. Back up Postgres before migrations, reloads, imports, and teardown.
2. Back up the runtime state file with filesystem permissions preserved.
3. Store backups encrypted and access-controlled.
4. Keep the spec revision that produced the world.
5. Test restore into an isolated environment before relying on a backup plan.

Disposable local/dev environments may be rebuilt from specs and migrations instead of restored.

## Restore guidance

Prefer importing a redacted compatible bundle, then rotating or re-entering live credentials. If restoring from raw runtime state, ensure it came from the same trusted operator environment and compatible schema version.
