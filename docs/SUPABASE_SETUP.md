# NetEngine Supabase Setup Guide

This guide walks you through setting up and configuring a Supabase project for use with NetEngine.

## Overview

NetEngine supports two database backends:

1. **Local PostgreSQL** (default): Use `docker-compose.yml` for local development
2. **Supabase Cloud**: Use a managed Supabase project for staging/production

This guide focuses on Supabase Cloud setup.

---

## Prerequisites

- A [Supabase account](https://app.supabase.com) (free tier available)
- A Supabase project created
- `psql` CLI tool (PostgreSQL client) installed locally
- Python 3.13+ (for Python setup script)
- Poetry (for NetEngine)

### Install PostgreSQL Client

```bash
# macOS
brew install postgresql

# Ubuntu/Debian
sudo apt-get install postgresql-client

# Windows (via WSL)
sudo apt-get install postgresql-client
```

---

## Getting Your Supabase Credentials

### Step 1: Get Your Project URL

1. Go to [app.supabase.com](https://app.supabase.com) and sign in
2. Select your project
3. Go to **Settings → API** (left sidebar)
4. Copy your **Project URL** (looks like: `https://xxxxx.supabase.co`)

### Step 2: Get Your Service Key

1. In **Settings → API**, scroll down to **Project API Keys**
2. Copy the **service_role** secret (NOT the `anon` key)
   - ⚠️ Keep this secret — it has full database access

### Step 3: Get Your Database Password

1. Go to **Settings → Database**
2. Under **Connection Info**, you'll see the database password
   - It's displayed only once during project creation
   - If you lost it, click **Reset database password**

### Step 4: Verify Database Access

Your database connection details are:
- **Host**: `db.[project-ref].supabase.co` (auto-extracted from URL)
- **Port**: `5432`
- **User**: `postgres`
- **Password**: From Step 3
- **Database**: `postgres`

Test connection from your local machine:

```bash
psql -h db.xxxxx.supabase.co -p 5432 -U postgres -d postgres -c "SELECT version();"
```

If this fails, check:
- Database password is correct
- Your IP is whitelisted (Supabase → Settings → Network)
- psql is installed

---

## Automatic Setup (Recommended)

### Using the Bash Script

The easiest way is to use the interactive bash setup script:

```bash
./scripts/setup_supabase.sh
```

This script will:
1. ✅ Prompt for your credentials
2. ✅ Test database connection
3. ✅ Run SQL migrations
4. ✅ Validate the setup
5. ✅ Save configuration to `.env`

**Interactive mode** (you'll be prompted):

```bash
./scripts/setup_supabase.sh
# → Asks for Supabase URL, Service Key, Database Password
# → Tests connection
# → Applies migrations
# → Saves to .env
```

**Non-interactive mode** (use environment variables):

```bash
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."
export SUPABASE_DB_PASSWORD="your_db_password"

./scripts/setup_supabase.sh --non-interactive
```

**Validate existing setup**:

```bash
./scripts/setup_supabase.sh --validate-only
```

### Using the Python Script

Alternatively, use the Python setup utility:

```bash
# Setup (interactive)
python scripts/setup_supabase.py --setup

# Or non-interactive
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."
export SUPABASE_DB_PASSWORD="your_db_password"
python scripts/setup_supabase.py --setup --non-interactive

# Validate only
python scripts/setup_supabase.py --validate-only

# Test connection
python scripts/setup_supabase.py --test-connection

# Run migrations only
python scripts/setup_supabase.py --migrate

# Verbose output
python scripts/setup_supabase.py --setup --verbose
```

---

## Manual Setup

If you prefer to set up manually or the automatic scripts don't work:

### Step 1: Create `.env` File

In your NetEngine project root, create or edit `.env`:

```bash
# Cloud Supabase
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
SUPABASE_DB_PASSWORD=your_db_password

# Disable local Postgres (comment out if using both)
# NETENGINE_DB_URL=...

# Other config
KEYCLOAK_ADMIN_PASSWORD=admin_password_here
NETENGINE_MOCK=false
NETENGINE_ZONE_DIR=./data/coredns
NETENGINE_STATE_FILE=netengine_state.json
```

### Step 2: Run Migrations

Apply the database schema:

```bash
poetry run python -m netengine.utils.run_migrations
```

This uses the credentials from `.env` to:
- Create all required tables
- Set up pgmq message queues
- Define helper functions

### Step 3: Verify Setup

Test that everything is configured correctly:

```bash
poetry run python -c "
import asyncio
from netengine.core.supabase_client import get_db

async def test():
    db = await get_db()
    # Try a simple query
    result = await db.table('runtime_state').select('*').limit(1).execute()
    print('✓ Database connected:', result)

asyncio.run(test())
"
```

---

## Starting NetEngine with Supabase

Once setup is complete:

```bash
# Make sure .env is sourced
export $(cat .env | xargs)

# Start a world
poetry run netengine up examples/minimal.yaml

# Check status
poetry run netengine status

# Tear down
poetry run netengine down
```

---

## Troubleshooting

### ❌ "Connection refused"

```
psql: error: could not translate host name "db.xxxxx.supabase.co" to address
```

- Check your URL is correct (should be `db.xxxxx.supabase.co`, not just `xxxxx.supabase.co`)
- Check your internet connection
- Try pinging the host: `ping db.xxxxx.supabase.co`

### ❌ "Password authentication failed"

```
psql: error: FATAL: password authentication failed for user "postgres"
```

- Verify database password from Supabase dashboard (Settings → Database)
- Make sure you're not mixing up the Postgres password with the Service Key
- Try resetting the password: Settings → Database → Reset database password

### ❌ "Connection timeout" or "No route to host"

```
psql: could not connect to server: No route to host
```

- Your IP is not whitelisted
- In Supabase, go to **Settings → Network**
- Add your IP address under **IPv4 address allowlist**
- Or allow all IPs: Add `0.0.0.0/0` (not recommended for production)

### ❌ "pgmq extension not found"

Some Supabase plans don't include the pgmq extension. You'll see:

```
ERROR: extension "pgmq" does not exist
```

This is usually not fatal — NetEngine can work without pgmq using fallback mechanisms. But if you need pgmq:
- Consider upgrading to Supabase Pro plan
- Or use local Postgres: `docker compose up -d db`

### ❌ "psql: command not found"

Install PostgreSQL client tools:

```bash
# macOS
brew install postgresql

# Ubuntu
sudo apt-get install postgresql-client

# Windows (WSL)
sudo apt-get install postgresql-client
```

### ✅ Verify Everything Works

After setup, run this test:

```bash
# Test Supabase connection
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."
export SUPABASE_DB_PASSWORD="password"

python scripts/setup_supabase.py --test-connection
python scripts/setup_supabase.py --validate-only
```

---

## Performance & Scaling

### Local Development

For development, use local Postgres (it's faster):

```bash
docker compose up -d db
poetry run python -m netengine.utils.run_migrations
poetry run netengine up examples/minimal.yaml
```

### Staging / Production

For staging or production, use Supabase Cloud:

```bash
./scripts/setup_supabase.sh
poetry run netengine up examples/prod-spec.yaml
```

### Scaling Considerations

| Plan | Suitable For | Query Limit |
|------|-------------|-----------|
| **Free** | Development, testing | 50k/month |
| **Pro** | Small production | Unlimited (overage charges) |
| **Enterprise** | Large production | Custom |

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPABASE_URL` | — | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | — | Service role API key |
| `SUPABASE_DB_HOST` | Inferred from URL | Database hostname |
| `SUPABASE_DB_PORT` | `5432` | Database port |
| `SUPABASE_DB_USER` | `postgres` | Database user |
| `SUPABASE_DB_PASSWORD` | — | Database password |
| `SUPABASE_DB_NAME` | `postgres` | Database name |
| `NETENGINE_DB_URL` | — | Alternative: full Postgres URI |

**When `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` are set**, NetEngine uses Supabase Cloud.

**When `NETENGINE_DB_URL` is set**, NetEngine uses local Postgres.

---

## Database Schema

NetEngine creates these tables:

- `runtime_state` — Orchestrator state (key-value store)
- `world_registry` — Registered worlds and capabilities
- `address_pools` — AND profile address allocations
- `address_leases` — AND instance IP assignments
- `domain_records` — Domain registry
- `operator_log` — API audit log

And these pgmq queues (if available):

- `dns_updates` → DNS zone updates
- `oidc_provisioning` → Identity setup
- `and_provisioning` → Network isolation setup
- `world_health` → Health check events

---

## Security Best Practices

1. **Keep `.env` secret**
   - Never commit `.env` to git
   - Use `.env.local` or `.env.production` for environment-specific secrets
   - Restrict file permissions: `chmod 600 .env`

2. **Use Service Role Keys in backend only**
   - Never expose `SUPABASE_SERVICE_KEY` to frontend
   - Always validate requests server-side

3. **Rotate credentials periodically**
   - Supabase: Settings → API → Rotate key
   - Database: Settings → Database → Reset password

4. **Monitor access**
   - Enable audit logging: Settings → Audit Logs
   - Review failed authentication attempts

5. **Network security**
   - Whitelist only necessary IPs
   - Use VPN for local development
   - Avoid whitelisting `0.0.0.0/0` in production

---

## Next Steps

- 📖 [NetEngine README](../README.md)
- 🏗️ [NetEngine Architecture](./decisions.md)
- 📚 [Supabase Docs](https://supabase.com/docs)
- 🔐 [PostgreSQL Security Guide](https://www.postgresql.org/docs/current/sql-syntax.html)

---

## Support

If you encounter issues:

1. Check the [troubleshooting section](#troubleshooting) above
2. Review [NetEngine architecture docs](./decisions.md)
3. Check [Supabase status page](https://status.supabase.com)
4. Open an issue on [GitHub](https://github.com/Forebase/NetEngine/issues)
