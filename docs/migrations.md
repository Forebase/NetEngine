# Database migration semantics

NetEngine applies SQL files from `migrations/` in filename order through the shared
`MigrationService`.

## Partial-failure behavior

- Each migration is recorded in `netengine_schema_migrations`.
- A migration is marked `applied` only after every SQL statement in that file completes.
- If a migration fails, the service records the failed filename, the current SQL statement
  context, and the database error, then stops immediately. Later migration files are not
  applied until the failed migration is corrected and rerun.
- Status output distinguishes:
  - `applied`: the file completed successfully and its checksum still matches.
  - `pending`: the file exists on disk and has no successful or failed record.
  - `failed`: the previous run failed for that file.
  - `checksum-drift`: the file was applied successfully, but its current checksum differs
    from the recorded checksum.

## Transaction boundaries

When PostgreSQL allows it, each migration file runs inside a single transaction. If any
statement fails in a transactional migration, PostgreSQL rolls back statements from that
file and NetEngine records the failure after the rollback.

Some PostgreSQL operations are not allowed inside transaction blocks and therefore cannot
be rolled back automatically. NetEngine detects common cases and executes those migration
files without wrapping them in a transaction. Avoid mixing non-transactional and ordinary
schema changes in the same file.

Known non-transactional operations include:

- `CREATE DATABASE` / `DROP DATABASE`
- `CREATE INDEX CONCURRENTLY`
- `REINDEX ... CONCURRENTLY`
- `VACUUM`
- `ALTER SYSTEM`
- `CREATE TABLESPACE` / `DROP TABLESPACE`

If one of these migrations fails partway through, manually inspect the database, repair or
reverse any already-executed statements, and rerun migrations.
