# mcp-job-assistant-server

An [MCP](https://modelcontextprotocol.io) server that exposes three job-search tools — semantic resume search, live company research, and cover letter generation — over HTTP, so any MCP client can discover and call them.

The tools themselves started life inside [`react-agent-toolkit`](https://github.com/syedilyasjaweed/react-agent-toolkit), where a single script both decided *which* tool to call and executed it. This project splits those responsibilities: the server hosts the tools and has no opinion about when to use them, and the reasoning loop moves to whatever MCP client connects — Claude Desktop, a custom agent, or anything else that speaks the protocol.

The server is containerized with Docker and deployed on **Azure Container Apps**, so clients connect to a public HTTPS endpoint instead of a local process. See [Deployment](#deployment).

---

## Why MCP

Wiring tools into an agent by hand doesn't scale past a few. Every new tool means new glue code, and that glue only works with the one agent you wrote it for.

MCP standardizes two things: how a client asks "what tools do you have?" and how it invokes one. Implement that interface once and your tools work with any compliant client. The practical payoff here is that `search_resume_bullets` stopped being a Python function locked inside one script and became a service anything can call over the network.

---

## Architecture

```
tools/*.py                    plain Python — Claude API, VoyageAI, Pinecone
      |  wrapped by @mcp.tool
server.py                     FastMCP — publishes tools in MCP shape
      |  mcp.http_app()
api.py                        FastAPI — /health + MCP mounted at /mcp
      |  guarded by
middleware.py                 API-key auth -> per-IP rate limiting
      |  served by
Uvicorn (ASGI)                one process, one port
      ^  connected to by
MCP client                    test_client.py, Claude Desktop, custom agent
```

Two details that are easy to get wrong and worth calling out:

**Lifespan sharing.** `FastAPI(lifespan=mcp_app.lifespan)` is required. Without it the MCP session manager never initializes and every request fails with `Task group is not initialized`. This is the most common mistake when mounting FastMCP inside FastAPI.

**Middleware order.** Starlette applies middleware in reverse registration order, so the last one added runs first. `RateLimitMiddleware` is registered before `AuthMiddleware`, which means auth runs first — an unauthenticated caller is rejected without consuming a rate-limit slot.

---

## Tools

Tool descriptions are generated from the function docstrings, and input schemas from the type hints, so what a client sees is whatever is written in `server.py`.

### `search_resume_bullets(query: str, top_k: int = 5) -> str`

Semantically searches resume bullets and returns the most relevant ones for a query. Matching is by meaning rather than keyword, so a query like "built production APIs" retrieves relevant bullets even when they never use that phrasing. Backed by VoyageAI embeddings and a Pinecone index.

### `research_company(company_name: str, force_refresh: bool = False) -> dict`

Researches a company using live web search and returns a structured profile: mission and values, recent news, tech stack, and culture notes. Results are cached for 7 days, so repeated calls for the same company are fast and cheap. Pass `force_refresh=True` to bypass the cache.

### `generate_cover_letter(company: str, role: str, jd_text: str, force_refresh: bool = False) -> dict`

Generates a tailored cover letter under 400 words for a specific application and writes it to disk. This tool re-runs resume matching and company research internally, so it can be called directly — the other two tools are useful beforehand for deciding whether a letter is worth generating, not as required setup.

---

## Setup

```bash
git clone https://github.com/syedilyasjaweed/mcp-job-assistant-server.git
cd mcp-job-assistant-server

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env               # then fill in the values
```

`.env` needs four keys:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude — company research and cover letter generation |
| `VOYAGE_API_KEY` | VoyageAI — embedding resume bullets and queries |
| `PINECONE_API_KEY` | Pinecone — vector index the resume search runs against |
| `API_KEY` | The key clients must send as `X-API-Key` to reach this server |

`API_KEY` is yours to invent — it gates access to *this* server and is unrelated to the three provider keys. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Running

```bash
uvicorn api:app --reload
```

The server comes up on `http://127.0.0.1:8000`. MCP traffic goes to `/mcp`; `/health` is a plain REST endpoint.

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

---

## Deployment

### Docker

```bash
docker build -t mcp-job-assistant .
docker run --rm -p 8000:8000 --env-file .env mcp-job-assistant

curl http://localhost:8000/health
# {"status":"ok"}
```

Two choices in the [Dockerfile](Dockerfile) are deliberate:

- **`requirements.txt` is copied before the source code.** Docker caches each layer, so dependencies are only reinstalled when `requirements.txt` changes, not on every code edit.
- **`.env` never enters the image.** It is excluded by `.dockerignore` and injected at runtime with `--env-file`, so secrets don't end up baked into a layer or pushed to a registry.

### Azure Container Apps

```
docker build  ->  Azure Container Registry  ->  Azure Container Apps
 (linux/amd64)     (image storage)              (runs the container, public HTTPS ingress)
```

Azure Container Apps was chosen for scale-to-zero pricing: when nothing is calling the server, no compute cost accrues. The tradeoff is a slower first request after an idle period while the container starts.

```bash
# 1. Resource group and container registry
az group create --name mcp-job-assistant-rg --location eastus
az acr create --resource-group mcp-job-assistant-rg --name mcpjobassistantacr --sku Basic
az acr login --name mcpjobassistantacr

# 2. Build for linux/amd64 and push.
#    The platform flag matters when building on Apple Silicon, which defaults to arm64.
docker buildx build --platform linux/amd64 \
  -t mcpjobassistantacr.azurecr.io/mcp-job-assistant:v1 --push .

# 3. Container Apps environment and the app itself
az containerapp env create \
  --name mcp-job-assistant-env \
  --resource-group mcp-job-assistant-rg \
  --location eastus

az containerapp create \
  --name mcp-job-assistant-app \
  --resource-group mcp-job-assistant-rg \
  --environment mcp-job-assistant-env \
  --image mcpjobassistantacr.azurecr.io/mcp-job-assistant:v1 \
  --registry-server mcpjobassistantacr.azurecr.io \
  --target-port 8000 --ingress external \
  --min-replicas 0
  # plus registry credentials or a managed identity with AcrPull

# 4. Runtime configuration: the four keys from .env
az containerapp update \
  --name mcp-job-assistant-app \
  --resource-group mcp-job-assistant-rg \
  --set-env-vars ANTHROPIC_API_KEY=<value> VOYAGE_API_KEY=<value> \
                 PINECONE_API_KEY=<value> API_KEY=<value>

# 5. Find the public address and check it
az containerapp show --name mcp-job-assistant-app --resource-group mcp-job-assistant-rg \
  --query properties.configuration.ingress.fqdn -o tsv

curl https://<fqdn>/health
```

**Shipping a change:** rebuild with a new tag, push, then point the app at it.

```bash
docker buildx build --platform linux/amd64 \
  -t mcpjobassistantacr.azurecr.io/mcp-job-assistant:v2 --push .

az containerapp update --name mcp-job-assistant-app --resource-group mcp-job-assistant-rg \
  --image mcpjobassistantacr.azurecr.io/mcp-job-assistant:v2
```

**Why host it instead of sharing keys.** The Anthropic, Voyage AI and Pinecone keys live only in the Azure app's environment and never leave the server. A client needs just the `API_KEY` to call the tools, so access can be given or revoked without handing out provider credentials.

---

## Connecting a client

`test_client.py` connects and lists the published tools:

```bash
python test_client.py
```

```
Discovered 3 tools:

- search_resume_bullets
  Semantically searches resume bullets and returns the most relevant
  input schema keys: ['query', 'top_k']
...
```

`test_client.py` points at `http://127.0.0.1:8000/mcp`. To test the deployed server, change `url` to `https://<fqdn>/mcp` and use the `API_KEY` set on the Azure app.

One wrinkle worth knowing if you write your own client: `Client("http://...")` accepts a URL string but gives you nowhere to attach headers, and this server requires `X-API-Key` on every request. Construct the transport explicitly instead:

```python
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

transport = StreamableHttpTransport(
    url="http://127.0.0.1:8000/mcp",
    headers={"X-API-Key": API_KEY},
)

async with Client(transport) as client:
    tools = await client.list_tools()
```

### What a session looks like in the logs

Because the MCP app is mounted at `/mcp`, Starlette answers requests to the bare path with a `307` to `/mcp/`, and the client follows it automatically. A healthy run looks like this:

```
POST /mcp   307 Temporary Redirect
POST /mcp/  200 OK              <- initialize handshake
GET  /mcp/  200 OK              <- SSE stream opens
POST /mcp/  202 Accepted        <- tools/list
DELETE /mcp/ 200 OK             <- session teardown
```

The redirects are normal, not a misconfiguration. Both spellings are covered by the middleware, since the guards are registered on the app rather than on routes.

---

## Auth and rate limiting

Both guards are registered on the FastAPI app rather than on individual routes, so they run before routing decides anything. That's what makes them cover `/mcp` and `/mcp/` automatically with no MCP-specific auth code.

**Auth** — every request needs a matching `X-API-Key` header. Missing or wrong returns `401` and never reaches a route or the MCP server underneath.

**Rate limiting** — 30 requests per 60-second rolling window, per client IP. Exceeding it returns `429`.

`/health` is exempt from both, since health checks are meant to be hit constantly and without credentials by load balancers and uptime monitors.

The rate limiter keeps counts in an in-memory dict, which is fine for local development and a single process. It resets on restart and doesn't share state across workers, so multiple Uvicorn workers or multiple containers behind a load balancer would each count independently. Moving to Redis is the fix, and is a prerequisite for scaling past one process.

On Azure this has two practical consequences: counts reset whenever the app scales to zero or restarts, and each replica counts separately. Traffic also reaches the container through Azure's ingress proxy, so the limiter needs to be checked against real client IPs rather than the proxy's address (see [Planned](#planned)).

---

## Troubleshooting

**`ERR_CONNECTION_REFUSED` in the browser** — nothing is listening on port 8000. The server isn't running; start it with `uvicorn api:app --reload`. A running server that rejects you returns `401`, not a refusal.

**`/mcp` shows an error in the browser** — expected, and not a bug. The address bar can't send an `X-API-Key` header, so auth returns `401`; and MCP is JSON-RPC over streamable HTTP, not a web page, so a plain `GET` isn't a valid request either. Use `/health` for a browser check and `test_client.py` for `/mcp`.

**`Task group is not initialized`** — FastAPI isn't sharing the MCP app's lifespan. Confirm `app = FastAPI(lifespan=mcp_app.lifespan)` in `api.py`.

**`401 Missing or invalid API key`** — `API_KEY` differs between the server's environment and the client's, or `.env` isn't being loaded. Both `api.py` and `test_client.py` must read the same value.

**`429 Rate limit exceeded`** — more than 30 requests from one IP inside 60 seconds. Wait out the window, or raise `RATE_LIMIT` in `middleware.py` for local testing.

**Container won't start on Azure / `exec format error`** — the image was built for the wrong CPU architecture. Apple Silicon builds `arm64` by default; Container Apps runs `linux/amd64`. Rebuild with `--platform linux/amd64`.

**`401` from the deployed `/mcp` but `/health` works** — the `API_KEY` environment variable isn't set on the Azure app, or differs from what the client sends. Set it with `az containerapp update --set-env-vars`.

---

## Planned

- **CI/CD** — GitHub Actions to build, tag, push and run `az containerapp update` on every change, replacing the manual steps above
- **Forwarded-header handling** — confirm the limiter sees real client IPs behind Azure's ingress (Uvicorn `--proxy-headers` and trusted forwarded IPs)

---

## Project layout

```
mcp-job-assistant-server/
├── tools/
│   ├── resume_search.py       # VoyageAI + Pinecone semantic search
│   ├── company_research.py    # Claude + web search, 7-day cache
│   └── cover_letter.py        # full generation pipeline
├── server.py                  # FastMCP instance, @mcp.tool registrations
├── api.py                     # FastAPI app, mounts MCP at /mcp
├── middleware.py              # AuthMiddleware, RateLimitMiddleware
├── test_client.py             # MCP client for verifying the server
├── Dockerfile                 # python:3.11-slim, dependency layer cached
├── .dockerignore              # keeps .env and local files out of the image
├── requirements.txt
└── .env.example
```