"""
FastAPI wrapper around the MCP server.

This is the "served over HTTP via FastAPI" layer from your roadmap. One
process, one port: FastAPI owns plain REST endpoints like /health, and MCP
traffic is mounted underneath at /mcp. Auth/rate-limiting middleware (added
in a later step) wraps the whole app, so it protects the MCP endpoint too
without any MCP-specific auth code.

Run locally with:
    uvicorn api:app --reload
"""
from fastapi import FastAPI

from server import mcp

# mcp.http_app() builds the actual ASGI app that speaks the MCP protocol
# over streamable HTTP. path="/" means it answers at the mount point
# itself (so mounted at /mcp, requests land on /mcp, not /mcp/mcp).
mcp_app = mcp.http_app(path="/")

# FastAPI must share the MCP app's lifespan, or the session manager behind
# it never initializes and every request fails with a "Task group is not
# initialized" error. This is the single most common mistake when mounting
# FastMCP inside FastAPI.
app = FastAPI(title="Job Assistant MCP Server", lifespan=mcp_app.lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/mcp", mcp_app)
