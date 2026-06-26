# NetEngine Setup Scripts

This directory contains setup and configuration scripts for NetEngine, with emphasis on Supabase cloud database configuration.

## Scripts

### `setup_supabase.sh` — Bash Setup Script (Recommended)

Interactive shell script for setting up Supabase with NetEngine.

**Usage:**

```bash
# Interactive setup (prompts for credentials)
./setup_supabase.sh

# Validate existing setup
./setup_supabase.sh --validate-only

# Non-interactive (use environment variables)
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."
export SUPABASE_DB_PASSWORD="password"
./setup_supabase.sh --non-interactive

# With verbose output
./setup_supabase.sh --verbose

# Skip pgmq setup (if not available in your Supabase plan)
./setup_supabase.sh --skip-pgmq

# Show help
./setup_supabase.sh --help
```

**Features:**

- ✅ Interactive credential collection
- ✅ Database connection testing
- ✅ SQL migration execution
- ✅ Configuration validation
- ✅ Automatic `.env` setup
- ✅ Color-coded output
- ✅ Error recovery
- ✅ Detailed help messages

---

### `setup_supabase.py` — Python Setup Module

Programmatic setup utility that can be used standalone or imported as a module.

**Usage:**

```bash
# Full setup
python scripts/setup_supabase.py --setup

# Validate only
python scripts/setup_supabase.py --validate-only

# Test connection
python scripts/setup_supabase.py --test-connection

# Run migrations only
python scripts/setup_supabase.py --migrate

# With environment variables (non-interactive)
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."
export SUPABASE_DB_PASSWORD="password"
python scripts/setup_supabase.py --setup --non-interactive

# Verbose output
python scripts/setup_supabase.py --setup --verbose
```

**Features:**

- ✅ Programmatic setup (can be imported)
- ✅ Detailed error reporting
- ✅ Connection testing
- ✅ Schema validation
- ✅ Environment file management
- ✅ Type hints (Python 3.13+)
- ✅ Unix exit codes

**As a Python Module:**

```python
from scripts.setup_supabase import SupabaseConfig, SupabaseSetup

# Create configuration
config = SupabaseConfig(
    url="https://xxxxx.supabase.co",
    service_key="eyJ...",
    db_host="db.xxxxx.supabase.co",
    db_port=5432,
    db_user="postgres",
    db_password="your_password"
)

# Run setup
setup = SupabaseSetup(config, verbose=True)
if setup.test_connection():
    setup.run_migrations()
    setup.validate()
    setup.save_env()
```

---

### `test_supabase_setup.sh` — Test Suite

Validates the setup scripts and environment prerequisites.

**Usage:**

```bash
# Run all tests
./test_supabase_setup.sh

# Shows:
# - Script existence and permissions
# - Syntax validation
# - Help text availability
# - Documentation presence
# - Environment requirements
```

---

### `install_pgmq.sh` — PostgreSQL Extension Installer

Installs the pgmq (Postgres Message Queue) extension for local Postgres development.

**Note:** This runs automatically in `docker-compose.yml`, so you typically don't need to run it manually.

**Usage:**

```bash
# For local Postgres (already integrated in docker-compose)
docker compose exec postgres bash /docker-entrypoint-initdb.d/install_pgmq.sh
```

---

## Quick Start

### For Cloud (Supabase)

```bash
# 1. Run interactive setup
./scripts/setup_supabase.sh

# 2. Verify environment
cat .env | grep SUPABASE

# 3. Apply migrations
poetry run python -m netengine.utils.run_migrations

# 4. Start NetEngine
poetry run netengine up examples/minimal.yaml
```

### For Local Development

```bash
# 1. Start local Postgres
docker compose up -d db

# 2. Apply migrations
poetry run python -m netengine.utils.run_migrations

# 3. Start NetEngine
poetry run netengine up examples/minimal.yaml
```

---

## Environment Variables

All scripts use these environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes* | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes* | Service role API key |
| `SUPABASE_DB_PASSWORD` | Yes* | Database password |
| `SUPABASE_DB_HOST` | No | Database hostname (auto-inferred) |
| `SUPABASE_DB_PORT` | No | Database port (default: 5432) |
| `SUPABASE_DB_USER` | No | Database user (default: postgres) |

\* Required for Supabase setup. Not needed for local Postgres setup.

---

## Troubleshooting

### psql: command not found

Install PostgreSQL client tools:

```bash
# macOS
brew install postgresql

# Ubuntu/Debian
sudo apt-get install postgresql-client

# Windows (WSL)
sudo apt-get install postgresql-client
```

### Connection refused

```bash
# Check your Supabase URL format
# Should be: https://xxxxx.supabase.co (not db.xxxxx.supabase.co)

# Test connection manually
psql -h db.xxxxx.supabase.co -p 5432 -U postgres -d postgres -c "SELECT 1;"
```

### pgmq extension not found

Not all Supabase plans include pgmq. If you see `ERROR: extension "pgmq" does not exist`:

```bash
# Option 1: Skip pgmq setup
./scripts/setup_supabase.sh --skip-pgmq

# Option 2: Use local Postgres instead
docker compose up -d db
poetry run python -m netengine.utils.run_migrations

# Option 3: Upgrade to Supabase Pro plan
```

### Password authentication failed

```bash
# Verify your database password
# 1. Go to Supabase dashboard
# 2. Settings → Database → Connection Info
# 3. Copy the password

# Test with psql
export PGPASSWORD="your_password"
psql -h db.xxxxx.supabase.co -p 5432 -U postgres -d postgres -c "SELECT 1;"
```

---

## Documentation

For complete setup guidance, see [docs/SUPABASE_SETUP.md](../docs/SUPABASE_SETUP.md).

---

## Security

- ✅ Scripts validate credentials before use
- ✅ `.env` file is created with restricted permissions (600)
- ✅ Passwords are not echoed to console (use prompt_secret)
- ✅ No credentials in command-line history
- ✅ Service keys are handled securely

**Never commit `.env` to git** — add it to `.gitignore`.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Setup/validation failed |
| `2` | Invalid arguments |
| `130` | Interrupted by user (Ctrl+C) |

---

## Development

To modify the setup scripts:

1. **Bash changes**: Update `setup_supabase.sh`, test with `bash -n script.sh`
2. **Python changes**: Update `setup_supabase.py`, test with `python3 -m py_compile script.py`
3. **Run tests**: `./test_supabase_setup.sh`
4. **Integration test**: `./setup_supabase.sh --validate-only` (requires valid Supabase credentials)

---

## License

MIT — See [LICENSE](../LICENSE)
