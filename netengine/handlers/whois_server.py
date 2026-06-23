import asyncio


class WHOISServer:
    def __init__(self, host: str = "10.0.0.9", port: int = 43):
        """Defaults match WHOISConfig spec defaults; callers should pass spec values."""
        self.host = host
        self.port = port
        self._db = None

    async def _get_db(self):
        if self._db is None:
            from netengine.core.supabase_client import get_db

            self._db = await get_db()
        return self._db

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        data = await reader.read(1024)
        query = data.decode().strip()
        response = await self.lookup(query)
        writer.write(response.encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def lookup(self, query: str) -> str:
        """Lookup domain in domain_records and world_registry."""
        db = await self._get_db()
        result = (
            await db.table("domain_records")
            .select("domain, org_name, ns_records, created_at")
            .eq("domain", query)
            .execute()
        )
        if not result.data:
            return f"No match for {query}\n"
        row = result.data[0]
        org_result = (
            await db.table("world_registry")
            .select("capabilities")
            .eq("org_name", row["org_name"])
            .execute()
        )
        caps = org_result.data[0]["capabilities"] if org_result.data else []
        lines = [
            f"Domain: {row['domain']}",
            f"Registrar: NetEngines",
            f"Owner: {row['org_name']}",
            f"Name Servers: {', '.join(row['ns_records'])}",
            f"Created: {row['created_at']}",
            f"Capabilities: {', '.join(caps)}",
            "\n",
        ]
        return "\n".join(lines)

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        print(f"WHOIS server listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()
