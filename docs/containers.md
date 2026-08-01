# Appwrite Assistant — LangGraph container notes

Shared long-running assistant engine for Cloud `/v1/assistant`. Cloud keeps HTTP, DB, Realtime, and the worker; this repo owns the LangGraph runtime image.

## Model

- One shared service (`appwrite-assistant`) on port `8000`.
- Auth via `ASSISTANT_API_KEY` → header `X-Session-API-Key`.
- Local UI (`appwrite-assistant-ui` on `:3001`) proxies `/api` to the engine. Not for Cloud ingress.
- **No host shell.** Tools: calculator, current_time, `web_search`, `browser_fetch` (Playwright; public https), and `sandbox_exec` stub (Function MCP later).
- Supervisor routes to `appwrite` / `researcher` / `worker` subagents (LangGraph).

## Cloud ConfigMap keys (planned)

| ConfigMap / `.env` | Container env |
|--------------------|---------------|
| `_APP_ASSISTANT_API_KEY` | `ASSISTANT_API_KEY` |
| `_APP_ASSISTANT_LLM_API_KEY` | `LLM_API_KEY` |
| `_APP_ASSISTANT_LLM_MODEL` | `LLM_MODEL` |

Worker endpoint: `http://appwrite-assistant:8000`.

## Before production

- [ ] Persist conversations in Cloud (this POC uses in-memory store + MemorySaver)
- [ ] Wire `sandbox_exec` to per-project Function sandbox MCP
- [ ] Pin dependency versions / image tags in CI
- [ ] Publish image to `ghcr.io/eldadfux/openhands`
- [ ] Restrict CORS and never expose the engine publicly
