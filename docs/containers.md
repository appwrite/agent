# OpenHands Agent Server — container notes (Appwrite Cloud)

Shared long-running Agent Server for Cloud `/v1/assistant`. Cloud keeps HTTP, DB, Realtime, and the worker; this repo owns the engine image.

## Model

- One shared service (`appwrite-openhands`), not per-conversation DockerWorkspace pods.
- Port `8000`, probes `/health` and `/ready`.
- Auth via `OH_SESSION_API_KEYS_*` → client header `X-Session-API-Key`.
- No public ingress.

## Cloud ConfigMap keys (planned)

| ConfigMap / `.env` | Container env |
|--------------------|---------------|
| `_APP_OPENHANDS_SESSION_API_KEY` | `OH_SESSION_API_KEYS_0` |
| `_APP_OPENHANDS_SECRET_KEY` | `OH_SECRET_KEY` |
| `_APP_OPENHANDS_LLM_API_KEY` | `LLM_API_KEY` |

Worker will use `_APP_OPENHANDS_ENDPOINT=http://appwrite-openhands:8000`.

## Before production

- [ ] Pin base image tag (no `latest-python`)
- [ ] Publish CI image to `ghcr.io/eldadfux/openhands`
- [ ] Disable host shell / local workspace exec (Function sandbox MCP only)
- [ ] Prefer minimal agent-server target (no VSCode/VNC) if validated
- [ ] Single replica or sticky workspace strategy
