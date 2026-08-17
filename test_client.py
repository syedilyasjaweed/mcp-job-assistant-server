import asyncio
from fastmcp import Client


async def main():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        tools = await client.list_tools()
        print(f"Discovered {len(tools)} tools:\n")
        for t in tools:
            print(f"- {t.name}")
            print(f"  {t.description.strip().splitlines()[0]}")
            print(f"  input schema keys: {list(t.inputSchema.get('properties', {}).keys())}\n")


asyncio.run(main())
