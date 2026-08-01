"""Stateless assistant HTTP API — all durable context is supplied by the caller."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.auth import require_session_key
from app.config import get_settings
from app.graph.inspect import HISTORY_WINDOW, agent_settings_snapshot
from app.graph.stream import run_turn_stream

router = APIRouter()


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""


class InlineAttachment(BaseModel):
    id: str | None = None
    name: str = Field(..., min_length=1)
    mime: str | None = None
    size: int | None = None
    kind: str | None = None
    content_base64: str = Field(..., min_length=1)


class McpConnection(BaseModel):
    """Full MCP server definition + credentials for this turn only."""

    id: str = Field(..., min_length=1)
    name: str | None = None
    url: str | None = None
    description: str = ""
    tokens: dict[str, Any] | None = None
    client_info: dict[str, Any] | None = None


class TurnRequest(BaseModel):
    """One agent turn. Proxy/UI owns persistence; the engine keeps nothing."""

    message: str = ""
    history: list[HistoryMessage] = Field(default_factory=list)
    attachments: list[InlineAttachment] = Field(default_factory=list)
    mcp_connections: list[McpConnection] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_message_or_attachments(self) -> TurnRequest:
        if not self.message.strip() and not self.attachments:
            raise ValueError("message or attachments required")
        return self


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _trim_history(history: Sequence[HistoryMessage]) -> list[dict[str, str]]:
    rows = [{"role": h.role, "content": h.content} for h in history]
    return rows[-HISTORY_WINDOW:]


async def _stream_turn(body: TurnRequest) -> AsyncIterator[str]:
    try:
        async for event in run_turn_stream(
            message=body.message,
            history=_trim_history(body.history),
            attachments=[a.model_dump(exclude_none=True) for a in body.attachments],
            mcp_connections=[
                c.model_dump(exclude_none=True) for c in body.mcp_connections
            ],
        ):
            yield _sse(event)
    except ValueError as exc:
        yield _sse({"type": "error", "detail": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "detail": str(exc)})
        return
    yield _sse({"type": "complete"})


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    settings = get_settings()
    ok = bool(settings.llm_api_key)
    return {"ready": ok, "llm_configured": ok}


@router.get("/api/meta", dependencies=[Depends(require_session_key)])
async def meta():
    """Runtime inspection (secrets masked). No connection state."""
    return agent_settings_snapshot()


@router.post("/api/turn", dependencies=[Depends(require_session_key)])
async def turn(body: TurnRequest):
    """Run one turn and stream SSE events. Stateless — context comes from the body."""
    if not get_settings().llm_api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY is not configured")
    return StreamingResponse(
        _stream_turn(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
