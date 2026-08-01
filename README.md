# Appwrite Assistant (LangGraph)

POC assistant engine for Appwrite Cloud `/v1/assistant`, built on [LangGraph](https://github.com/langchain-ai/langgraph).

**Stateless by design.** Cloud (or any proxy) owns conversation persistence, Realtime, and auth. The UI/client owns MCP OAuth and credentials. This service is the agent runtime only — every turn receives the context it needs over the API.

## What you get

- **Supervisor + subagents** (`appwrite`, `researcher`, `worker`) via LangGraph
- **Appwrite expert** with official [agent-skills](https://github.com/appwrite/agent-skills) vendored under `.agents/skills/`
- **Safe tools by default** — no host shell for the model
- Tools: `calculator`, `current_time`, `web_search`, `browser_fetch`, `appwrite_skill`, `sandbox_exec` stub, plus MCP tools from credentials on the turn
- FastAPI + shadcn chat UI

## Quick start

```bash
cp .env.example .env
# set ASSISTANT_API_KEY and LLM_API_KEY

docker compose up --build -d

curl -s http://127.0.0.1:8000/health
curl -sN -H "X-Session-API-Key: $ASSISTANT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 17*19?","history":[]}' \
  http://127.0.0.1:8000/api/turn
```

- API docs: http://127.0.0.1:8000/docs
- UI: http://127.0.0.1:3001

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Liveness |
| GET | `/ready` | LLM configured? |
| GET | `/api/meta` | Auth — runtime inspection |
| POST | `/api/turn` | Auth — one turn (SSE) |

### Turn request

```json
{
  "message": "What is 17*19?",
  "history": [
    { "role": "user", "content": "Hi" },
    { "role": "assistant", "content": "Hello!" }
  ],
  "attachments": [
    { "name": "notes.txt", "mime": "text/plain", "content_base64": "..." }
  ],
  "mcp_connections": [
    {
      "id": "appwrite",
      "name": "Appwrite",
      "url": "https://mcp.appwrite.io/",
      "tokens": { "access_token": "...", "refresh_token": "..." },
      "client_info": { "client_id": "..." }
    }
  ]
}
```

SSE events include `route`, `subagent_*`, `tool_*`, `token`, `mcp_credentials` (refreshed tokens for the client to store), `done`, `complete`.

## MCP OAuth (client-owned)

The engine does **not** run OAuth. The UI (or production proxy) does:

1. Discover protected-resource + authorization-server metadata
2. Dynamic client registration (public client + PKCE)
3. Browser authorize → callback at `{origin}/oauth/mcp/callback`
4. Store `tokens` + `client_info` in localStorage
5. Send them on every `/api/turn` as `mcp_connections`

Production Appwrite should own steps 1–4 and replay credentials on each turn the same way.

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `ASSISTANT_API_KEY` | yes (prod) | Clients send `X-Session-API-Key` |
| `LLM_API_KEY` | yes | Model provider API key |
| `LLM_MODEL` | no | Default `openai/gpt-4o` |
| `LLM_BASE_URL` | no | Optional OpenAI-compatible base URL |
| `WEB_SEARCH_ENABLED` | no | Headless browser web search (default `true`) |
| `ATTACHMENTS_MAX_BYTES` | no | Max inline attachment size (default 10MB) |

## Security

- Do not expose this container on a public Gateway/HTTPRoute.
- The model cannot run host shell commands.
- Unauthenticated mode (empty API key) is for local smoke tests only.

## Appwrite skills

```bash
./scripts/update-appwrite-skills.sh
docker compose up --build -d assistant
```

## Local (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export LLM_API_KEY=... ASSISTANT_API_KEY=...
uvicorn app.main:app --reload --port 8000
```
