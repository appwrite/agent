# Appwrite Assistant (LangGraph)

POC assistant engine for Appwrite Cloud `/v1/assistant`, built on [LangGraph](https://github.com/langchain-ai/langgraph).

Cloud owns conversation persistence, Realtime, and auth. This service is the agent runtime only — cluster/compose-internal, never public.

## What you get

- **Supervisor + subagents** (`appwrite`, `researcher`, `worker`) via LangGraph
- **Appwrite expert** with official [agent-skills](https://github.com/appwrite/agent-skills) vendored under `.agents/skills/` (CLI + 10 SDK languages), loaded on demand via `appwrite_skill`
- **Safe tools by default** — no host shell / no container `curl`/`bash` for the model
- Tools today: `calculator`, `current_time`, `web_search` (browser, no API key), `browser_fetch` (any public https), `appwrite_skill`, `sandbox_exec` stub (Function MCP later)
- FastAPI HTTP API + **shadcn chat UI** ([MessageScroller / Message / Bubble / Marker](https://ui.shadcn.com/docs/changelog/2026-06-chat-components)) with live tool/subagent markers and SSE streaming

## Quick start

```bash
cp .env.example .env
# set ASSISTANT_API_KEY and LLM_API_KEY

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
| `WEB_SEARCH_ENABLED` | no | Headless browser web search (default `true`) |
| `ASSISTANT_UI_PORT` | no | Host port for UI (default `3001`) |

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
- The model **cannot** run host shell commands. Outbound browsing via `web_search` / `browser_fetch` (Playwright; public https only).
- `sandbox_exec` is a stub — Cloud should execute code only inside per-project Function sandboxes.
- Unauthenticated mode (empty API key) is for local smoke tests only.

## Appwrite skills

Official skills from [appwrite/agent-skills](https://github.com/appwrite/agent-skills) are vendored under `.agents/skills/` (tracked via `skills-lock.json`).

Refresh when upstream publishes updates:

```bash
./scripts/update-appwrite-skills.sh          # update + install any new skills
./scripts/update-appwrite-skills.sh list     # show installed
./scripts/update-appwrite-skills.sh check    # lockfile summary
```

Requires Node/`npx`. Then commit `.agents/skills/` + `skills-lock.json` and rebuild:

```bash
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
