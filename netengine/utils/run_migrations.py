import os
from pathlib import Path
from supabase import create_client

async def apply_migrations():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    client = create_client(url, key)

    sql_path = Path(__file__).parent.parent / "migrations" / "001_initial.sql"
    sql = sql_path.read_text()

    # Execute via Supabase's SQL API (REST)
    # The endpoint is /api/rest/v1/rpc/exec_sql?query=...
    # But simpler: use the `supabase` Python client's `rpc` call if you have a function.
    # For cloud Supabase, you can run it via the dashboard or use `psql`.
    # For MVP, we'll just run it synchronously with a subprocess (psql) or use the SQL API.
    # We'll use aiohttp to POST to /api/rest/v1/rpc/exec_sql
    import aiohttp
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    payload = {"query": sql}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{url}/api/rest/v1/rpc/exec_sql", json=payload, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Migration failed: {await resp.text()}")