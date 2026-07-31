# Appwrite Assistant (LangGraph)

POC assistant engine for Appwrite Cloud `/v1/assistant`, built on [LangGraph](https://github.com/langchain-ai/langgraph).

Cloud owns conversation persistence, Realtime, and auth. This service is the agent runtime only — cluster/compose-internal, never public.

## What you get

- **Supervisor + subagents** (`researcher`, `worker`) via LangGraph
- **Safe tools by default** — no host shell / no container `curl`/`bash` for the model
- Tools today: `calculator`, `current_time`, `google_search` (browser, no API key), `browser_fetch` (any public https), `sandbox_exec` stub (Function MCP later)
- FastAPI HTTP API + **shadcn chat UI** ([MessageScroller / Message / Bubble / Marker](https://ui.shadcn.com/docs/changelog/2026-06-chat-components)) with live tool/subagent markers and SSE streaming

## Quick start

```bash
cp .env.example .env
# set ASSISTANT_API_KEY (or OH_SESSION_API_KEYS_0) and LLM_API_KEY

docker compose up --build -d

curl -s http://127.0.0.1:8000/health
curl -s -H "X-Session-API-Key: $ASSISTANT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 17*19?"}' \
  http://127.0.0.1:8000/api/conversations
```

- API docs: http://127.0.0.1:8000/docs
- UI: http://127.0.0.1:3001

## Services

| Service | Role |
|---------|------|
| `assistant` | LangGraph + FastAPI (`:8000`) |
| `ui` | React + shadcn chat UI; nginx proxies `/api` → assistant (`:3001`) |

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `ASSISTANT_API_KEY` | yes (prod) | Clients send `X-Session-API-Key` |
| `LLM_API_KEY` | yes | Model provider API key |
| `LLM_MODEL` | no | Default `openai/gpt-4o` (also accepts bare `gpt-4o`) |
| `LLM_BASE_URL` | no | Optional OpenAI-compatible base URL |
| `GOOGLE_SEARCH_ENABLED` | no | Browser Google search with Bing/Brave fallback (default `true`) |
| `ASSISTANT_UI_PORT` | no | Host port for UI (default `3001`) |

Legacy: `OH_SESSION_API_KEYS_0` is still accepted as the session key.

## API (POC)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Liveness |
| GET | `/ready` | LLM configured? |
| GET | `/api/conversations/count` | Auth |
| GET | `/api/conversations` | Auth |
| POST | `/api/conversations` | `{ "message": "..." }` — create + run |
| POST | `/api/conversations/stream` | SSE: create + stream tools/subagents/tokens |
| POST | `/api/conversations/{id}/messages` | Continue thread |
| POST | `/api/conversations/{id}/messages/stream` | SSE continue thread |
| GET | `/api/conversations/{id}` | Fetch thread |

SSE event types include `route`, `subagent_start` / `subagent_end`, `tool_start` / `tool_end`, `token`, `done`.

## Security

- Do not expose this container on a public Gateway/HTTPRoute.
- The model **cannot** run host shell commands. Outbound browsing via `google_search` / `browser_fetch` (Playwright; public https only).
- `sandbox_exec` is a stub — Cloud should execute code only inside per-project Function sandboxes.
- Unauthenticated mode (empty API key) is for local smoke tests only.

## Local (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export LLM_API_KEY=... ASSISTANT_API_KEY=...
uvicorn app.main:app --reload --port 8000
```
