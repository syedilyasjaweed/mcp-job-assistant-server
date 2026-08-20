"""
Auth + rate-limiting middleware.

Both classes below are "wrap the whole building" guards, not "stand at one
door" guards — they're registered on the FastAPI `app` itself (see api.py),
so every request passes through them before FastAPI even decides which
route it matches. That's what makes them cover /mcp AND /mcp/ automatically,
without needing any MCP-specific code: the guard doesn't care which sign
the visitor walked in under, only that they walked in at all.

/health is exempted in both classes on purpose — health checks are meant
to be hit constantly and without a key, e.g. by a load balancer or uptime
monitor. Everything else (including both /mcp spellings) is protected.
"""
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

API_KEY = os.getenv("API_KEY")

RATE_LIMIT = 30       # max requests allowed per window
RATE_WINDOW = 60      # window size, in seconds


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Checks for a secret key on an incoming header, X-API-Key. No key or
    the wrong key -> 401, request never reaches your routes or the MCP
    server underneath.
    """
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)  # health checks stay public

        provided_key = request.headers.get("X-API-Key")
        if not API_KEY or provided_key != API_KEY:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key"},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Counts requests per IP address in a rolling time window using a
    plain in-memory dict. Good enough for local dev and single-process
    deployments. NOTE: this resets on server restart and does not share
    state across multiple processes/containers — once you're running
    more than one uvicorn worker or multiple containers behind a load
    balancer, this needs to move to something shared like Redis, since
    each process would otherwise count independently.
    """
    def __init__(self, app):
        super().__init__(app)
        # client_ip -> list of timestamps of recent requests
        self.request_log = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)  # health checks aren't rate-limited

        client_ip = request.client.host
        now = time.time()

        # Drop timestamps older than the window - only what's recent counts
        recent = [t for t in self.request_log[client_ip] if now - t < RATE_WINDOW]
        self.request_log[client_ip] = recent

        if len(recent) >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {RATE_LIMIT} requests per {RATE_WINDOW}s"},
            )

        self.request_log[client_ip].append(now)
        return await call_next(request)
