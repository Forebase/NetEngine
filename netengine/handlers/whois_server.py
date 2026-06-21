import asyncio
from netengine.core.supabase_client import get_supabase

class WHOISServer:
    def __init__(self, host: str = "10.0.0.9", port: int = 43):
        self.host = host
        self.port = port
        self.supabase = get_supabase()

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
        # Query Supabase
        result = await self.supabase.table("domain_records")\
            .select("domain, org_name, ns_records, created_at")\
            .eq("domain", query)\
            .execute()
        if not result.data:
            return f"No match for {query}\n"
        row = result.data[0]
        # Get org details
        org_result = await self.supabase.table("world_registry")\
            .select("capabilities")\
            .eq("org_name", row["org_name"])\
            .execute()
        caps = org_result.data[0]["capabilities"] if org_result.data else []
        lines = [
            f"Domain: {row['domain']}",
            f"Registrar: NetEngines",
            f"Owner: {row['org_name']}",
            f"Name Servers: {', '.join(row['ns_records'])}",
            f"Created: {row['created_at']}",
            f"Capabilities: {', '.join(caps)}",
            "\n"
        ]
        return "\n".join(lines)

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        print(f"WHOIS server listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()