import asyncio
import os

from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

load_dotenv()  # reads .env so os.getenv() below can find the same API_KEY the server checks

API_KEY = os.getenv("API_KEY")

# Client() on its own only takes a plain URL string, with no way to attach
# custom headers. StreamableHttpTransport is the lower-level object that
# actually handles the HTTP connection, and IT accepts a headers dict - so
# we build one by hand and hand it to Client() instead of a bare URL.
transport = StreamableHttpTransport(
    url="http://127.0.0.1:8000/mcp",
    headers={"X-API-Key": API_KEY},
)


async def main():
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"Discovered {len(tools)} tools:\n")
        for t in tools:
            print(f"- {t.name}")
            print(f"  {t.description.strip().splitlines()[0]}")
            print(f"  input schema keys: {list(t.inputSchema.get('properties', {}).keys())}\n")


asyncio.run(main())