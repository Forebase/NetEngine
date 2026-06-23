"""
Async database client backed by asyncpg (local Postgres) or Supabase cloud.

Default: connects to NETENGINE_DB_URL (the Postgres container in docker-compose).
Override: set SUPABASE_URL + SUPABASE_SERVICE_KEY to use Supabase cloud PostgREST instead.

The public interface mirrors the subset of the Supabase Python SDK used in this codebase
so callers need no changes when switching backends:
    db = await get_db()
    result = await db.table("world_registry").upsert({...}).execute()
    result.data  # list[dict]
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

_pool: Optional[asyncpg.Pool] = None

# Primary key per table — needed to generate ON CONFLICT clauses for upsert.
_TABLE_PKS: Dict[str, str] = {
    "world_registry": "org_name",
    "address_pools": "profile",
    "address_leases": "and_name",
    "domain_records": "domain",
    "runtime_state": "key",
    "app_deployments": "id",
    "operator_log": "id",
}

# Positional argument order for each SQL helper function (mirrors migrations/001_initial.sql).
_RPC_ARG_ORDER: Dict[str, List[str]] = {
    "pgmq_send": ["queue_name", "message"],
    "pgmq_pop": ["queue_name", "timeout"],
    "pgmq_delete": ["queue_name", "msg_id"],
    "pgmq_read_by_id": ["queue_name", "msg_id"],
    "pgmq_metrics": ["queue_name"],
}


class _QueryResult:
    __slots__ = ("data",)

    def __init__(self, data: List[Dict[str, Any]]) -> None:
        self.data = data


class _RpcQuery:
    def __init__(self, pool: asyncpg.Pool, func_name: str, params: Dict[str, Any]) -> None:
        self._pool = pool
        self._func = func_name
        self._params = params

    async def execute(self) -> _QueryResult:
        arg_order = _RPC_ARG_ORDER.get(self._func, list(self._params.keys()))
        args = [self._params[k] for k in arg_order]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(args)))
        sql = f"SELECT {self._func}({placeholders})"
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(sql, *args)
        if result is None:
            return _QueryResult([])
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                pass
        if isinstance(result, dict):
            return _QueryResult([result])
        return _QueryResult([result])


class _TableQuery:
    def __init__(self, pool: asyncpg.Pool, table: str) -> None:
        self._pool = pool
        self._table = table
        self._op: Optional[str] = None
        self._data: Optional[Dict[str, Any]] = None
        self._filters: List[Tuple[str, Any]] = []
        self._cols = "*"
        self._limit: Optional[int] = None

    # ── Builder methods ────────────────────────────────────────────────────────

    def select(self, cols: str = "*") -> "_TableQuery":
        self._op = "select"
        self._cols = cols
        return self

    def insert(self, data: Dict[str, Any]) -> "_TableQuery":
        self._op = "insert"
        self._data = data
        return self

    def upsert(self, data: Dict[str, Any]) -> "_TableQuery":
        self._op = "upsert"
        self._data = data
        return self

    def update(self, data: Dict[str, Any]) -> "_TableQuery":
        self._op = "update"
        self._data = data
        return self

    def delete(self) -> "_TableQuery":
        self._op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "_TableQuery":
        self._filters.append((col, val))
        return self

    def limit(self, n: int) -> "_TableQuery":
        self._limit = n
        return self

    # ── Execution ──────────────────────────────────────────────────────────────

    async def execute(self) -> _QueryResult:
        async with self._pool.acquire() as conn:
            if self._op == "select":
                return await self._do_select(conn)
            if self._op == "insert":
                return await self._do_insert(conn)
            if self._op == "upsert":
                return await self._do_upsert(conn)
            if self._op == "update":
                return await self._do_update(conn)
            if self._op == "delete":
                return await self._do_delete(conn)
            raise ValueError(f"No operation set on _TableQuery for table '{self._table}'")

    def _where(self, offset: int = 0) -> Tuple[str, List[Any]]:
        if not self._filters:
            return "", []
        clauses = [f"{col} = ${i + offset + 1}" for i, (col, _) in enumerate(self._filters)]
        vals = [v for _, v in self._filters]
        return "WHERE " + " AND ".join(clauses), vals

    async def _do_select(self, conn: asyncpg.Connection) -> _QueryResult:
        where, params = self._where()
        limit_clause = f" LIMIT {self._limit}" if self._limit is not None else ""
        sql = f"SELECT {self._cols} FROM {self._table} {where}{limit_clause}".strip()
        rows = await conn.fetch(sql, *params)
        return _QueryResult([dict(r) for r in rows])

    async def _do_insert(self, conn: asyncpg.Connection) -> _QueryResult:
        data = self._data or {}
        cols = list(data.keys())
        vals = list(data.values())
        ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
        sql = f"INSERT INTO {self._table} ({', '.join(cols)}) VALUES ({ph}) RETURNING *"
        rows = await conn.fetch(sql, *vals)
        return _QueryResult([dict(r) for r in rows])

    async def _do_upsert(self, conn: asyncpg.Connection) -> _QueryResult:
        data = self._data or {}
        pk = _TABLE_PKS.get(self._table, "id")
        cols = list(data.keys())
        vals = list(data.values())
        ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != pk)
        do_clause = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
        sql = (
            f"INSERT INTO {self._table} ({', '.join(cols)}) VALUES ({ph}) "
            f"ON CONFLICT ({pk}) {do_clause} RETURNING *"
        )
        rows = await conn.fetch(sql, *vals)
        return _QueryResult([dict(r) for r in rows])

    async def _do_update(self, conn: asyncpg.Connection) -> _QueryResult:
        data = self._data or {}
        cols = list(data.keys())
        vals = list(data.values())
        set_parts = [f"{c} = ${i + 1}" for i, c in enumerate(cols)]
        where, where_vals = self._where(offset=len(cols))
        sql = f"UPDATE {self._table} SET {', '.join(set_parts)} {where} RETURNING *".strip()
        rows = await conn.fetch(sql, *vals, *where_vals)
        return _QueryResult([dict(r) for r in rows])

    async def _do_delete(self, conn: asyncpg.Connection) -> _QueryResult:
        where, params = self._where()
        sql = f"DELETE FROM {self._table} {where} RETURNING *".strip()
        rows = await conn.fetch(sql, *params)
        return _QueryResult([dict(r) for r in rows])


class AsyncDBClient:
    """Thin asyncpg-backed database client with a Supabase-compatible builder API."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def table(self, name: str) -> _TableQuery:
        return _TableQuery(self._pool, name)

    def rpc(self, func_name: str, params: Dict[str, Any]) -> _RpcQuery:
        return _RpcQuery(self._pool, func_name, params)


_DEFAULT_DB_URL = "postgresql://netengine:dev_password@localhost:5432/netengine"


async def get_local_db() -> AsyncDBClient:
    """Return an AsyncDBClient connected to the local Postgres instance."""
    global _pool
    if _pool is None:
        db_url = os.environ.get("NETENGINE_DB_URL", _DEFAULT_DB_URL)
        _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=10)
    return AsyncDBClient(_pool)


async def close_pool() -> None:
    """Close the connection pool — call on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
