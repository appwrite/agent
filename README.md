# Appwrite OpenHands Agent Server

POC container for the shared [OpenHands](https://docs.openhands.dev/) Agent Server used by Appwrite Cloud `/v1/assistant`.

Cloud owns conversation persistence, Realtime, and auth. This service is the coding-agent engine only — cluster/compose-internal, never public.

## Quick start

```bash
cp .env.example .env
# edit .env — set OH_SESSION_API_KEYS_0, OH_SECRET_KEY, LLM_API_KEY

docker compose up --build -d

curl -s http://127.0.0.1:8000/health
curl -s -H "X-Session-API-Key: $OH_SESSION_API_KEYS_0" \
  http://127.0.0.1:8000/api/conversations/count
```

API docs (when running): http://127.0.0.1:8000/docs

## Image

- Base: `ghcr.io/openhands/agent-server:latest-python` (minimal-ish Python variant)
- Binds `0.0.0.0:8000` for in-cluster access
- Workspace data under `/workspace`

Build / tag locally:

```bash
docker build -t ghcr.io/eldadfux/openhands:dev .
```

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `OH_SESSION_API_KEYS_0` | yes (prod) | Clients must send `X-Session-API-Key` |
| `OH_SECRET_KEY` | yes (prod) | Encrypts conversation secrets; keep stable |
| `LLM_API_KEY` | yes | Model provider API key |
| `LLM_MODEL` | no | Default `openai/gpt-4o` |

## Cloud integration (next)

Appwrite Cloud will call this service from `worker-assistant`:

- Endpoint: `http://appwrite-openhands:8000`
- Header: `X-Session-API-Key`
- MCP (later): `mcp.appwrite.io` + per-project Function sandbox MCP
- Host shell/exec tools must be disabled before multi-tenant production use

See the Cloud plan: `appwrite-labs/cloud` → `references/assistant-service.md`.

## Security notes

- Do not expose this container on a public Gateway/HTTPRoute.
- Unauthenticated mode is for local smoke tests only.
- Official agent-server images include local workspace/command tools — treat host-tool lockdown as a release blocker before sharing across tenants.
