"""HTTP API for MCP server connections and OAuth callback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, HttpUrl

from app.auth import require_session_key
from app.mcp.manager import get_mcp_manager
from app.mcp.registry import add_server, remove_server

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class AddServerRequest(BaseModel):
    id: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    url: HttpUrl
    description: str = ""


def _callback_html(*, ok: bool, title: str, message: str, server_id: str = "") -> str:
    status = "ok" if ok else "error"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; padding: 2rem;
           max-width: 28rem; margin: 10vh auto; color: #111; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
    p {{ color: #444; line-height: 1.5; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{message}</p>
  <script>
    try {{
      window.opener && window.opener.postMessage(
        {{ type: "mcp-oauth", status: "{status}", serverId: {server_id!r} }},
        "*"
      );
    }} catch (e) {{}}
    setTimeout(function () {{ window.close(); }}, 1200);
  </script>
</body>
</html>"""


@router.get("/servers", dependencies=[Depends(require_session_key)])
async def list_mcp_servers():
    return {"servers": get_mcp_manager().status_snapshot()}


@router.post("/servers", dependencies=[Depends(require_session_key)])
async def create_mcp_server(body: AddServerRequest):
    try:
        server = add_server(
            server_id=body.id,
            name=body.name,
            url=str(body.url),
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "server": {
            "id": server.id,
            "name": server.name,
            "url": server.url,
            "description": server.description,
            "builtin": False,
            "status": "disconnected",
            "tools": [],
        }
    }


@router.delete("/servers/{server_id}", dependencies=[Depends(require_session_key)])
async def delete_mcp_server(server_id: str):
    try:
        await get_mcp_manager().disconnect(server_id)
        remove_server(server_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Server not found") from exc
    return {"ok": True}


@router.post(
    "/servers/{server_id}/connect",
    dependencies=[Depends(require_session_key)],
)
async def connect_mcp_server(server_id: str):
    manager = get_mcp_manager()
    try:
        result = await manager.start_connect(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Server not found") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


@router.post(
    "/servers/{server_id}/disconnect",
    dependencies=[Depends(require_session_key)],
)
async def disconnect_mcp_server(server_id: str):
    try:
        await get_mcp_manager().disconnect(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Server not found") from exc
    return {"ok": True, "server_id": server_id}


@router.get("/oauth/callback")
async def mcp_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Browser redirect target for MCP OAuth (no session header — state is the CSRF guard)."""
    if error:
        detail = error_description or error
        return HTMLResponse(
            _callback_html(
                ok=False,
                title="Connection failed",
                message=f"Authorization was denied or failed: {detail}",
            ),
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            _callback_html(
                ok=False,
                title="Connection failed",
                message="Missing authorization code or state.",
            ),
            status_code=400,
        )
    try:
        server_id = await get_mcp_manager().complete_oauth(code=code, state=state)
    except KeyError:
        return HTMLResponse(
            _callback_html(
                ok=False,
                title="Connection expired",
                message="This sign-in link is unknown or expired. Try Connect again.",
            ),
            status_code=400,
        )
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            _callback_html(
                ok=False,
                title="Connection failed",
                message=str(exc),
            ),
            status_code=500,
        )
    return HTMLResponse(
        _callback_html(
            ok=True,
            title="Connected",
            message="You can close this window and return to the assistant.",
            server_id=server_id,
        )
    )
