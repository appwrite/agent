# Appwrite Assistant — LangGraph container notes

Shared long-running assistant engine for Cloud `/v1/assistant`. Cloud keeps HTTP, DB, Realtime, OAuth, and the worker; this repo owns the LangGraph runtime image.

## Model

- One shared service (`appwrite-assistant`) on port `8000`.
- Auth via `ASSISTANT_API_KEY` → header `X-Session-API-Key`.
- Local UI proxies `/api` to the engine (`:3001`). Not for Cloud ingress.
- **Stateless:** conversations, attachments, and MCP tokens are supplied per `POST /api/turn`. No durable volumes. No MCP OAuth on the engine.
- Supervisor routes to `appwrite` / `researcher` / `worker`.

## Proxy contract

`POST /api/turn` with `history`, inline `attachments` (`content_base64`), and `mcp_connections` (full server URL + tokens + client_info). Persist refreshed credentials from `mcp_credentials` SSE events.

`GET /api/meta` is optional inspection (suggested MCP URLs only).

## Cloud ConfigMap keys (planned)

| ConfigMap / `.env` | Container env |
|--------------------|---------------|
| `_APP_ASSISTANT_API_KEY` | `ASSISTANT_API_KEY` |
| `_APP_ASSISTANT_LLM_API_KEY` | `LLM_API_KEY` |
| `_APP_ASSISTANT_LLM_MODEL` | `LLM_MODEL` |

Worker endpoint: `http://appwrite-assistant:8000`.
