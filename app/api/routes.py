from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.auth import require_session_key
from app.config import get_settings
from app.graph.builder import get_graph
from app.graph.inspect import HISTORY_WINDOW, agent_settings_snapshot
from app.graph.stream import stream_turn
from app.store import store

router = APIRouter()


class CreateConversationRequest(BaseModel):
    message: str = Field(..., min_length=1)
    title: str | None = None


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1)


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    message_count: int
    last_response: str
    events: list[dict]


def _clean_answer(text: str) -> str:
    for prefix in ("[researcher] ", "[worker] "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def _run_turn(conversation_id: str, message: str) -> str:
    graph = get_graph()
    result = graph.invoke(
        {"messages": [HumanMessage(content=message)], "next": "", "handoffs": 0},
        config={"configurable": {"thread_id": conversation_id}, "recursion_limit": 24},
    )
    messages = result.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    content = getattr(last, "content", "")
    text = content if isinstance(content, str) else str(content)
    return _clean_answer(text)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _conversation_history(conversation_id: str, current_message: str) -> list[dict]:
    """Prior user/assistant turns for this conversation (exclude current user msg)."""
    conv = store.get(conversation_id)
    if not conv:
        return []
    history: list[dict] = []
    for event in conv.events:
        role = event.get("role")
        content = event.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        history.append({"role": role, "content": content})
    # routes append the user message before streaming; don't duplicate it.
    if (
        history
        and history[-1]["role"] == "user"
        and history[-1]["content"] == current_message
    ):
        history = history[:-1]
    # Keep the prompt bounded for the POC.
    return history[-HISTORY_WINDOW:]


async def _stream_conversation(
    conversation_id: str,
    message: str,
) -> AsyncIterator[str]:
    yield _sse({"type": "conversation", "id": conversation_id})
    activity: list[dict] = []
    answer = ""
    history = _conversation_history(conversation_id, message)

    try:
        async for event in stream_turn(message, history=history):
            # Persist a compact activity trail (skip high-volume tokens).
            if event.get("type") != "token":
                activity.append(event)
            if event.get("type") == "done":
                answer = event.get("answer") or ""
            yield _sse(event)
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "detail": str(exc)})
        return

    store.append_event(conversation_id, "assistant", answer, meta={"activity": activity})
    yield _sse({"type": "complete", "id": conversation_id, "answer": answer})


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    settings = get_settings()
    ok = bool(settings.llm_api_key)
    return {"ready": ok, "llm_configured": ok}


@router.get("/api/settings", dependencies=[Depends(require_session_key)])
async def agent_settings():
    """Inspect agent runtime settings (secrets masked)."""
    return agent_settings_snapshot()


@router.get("/api/conversations/count", dependencies=[Depends(require_session_key)])
async def conversations_count():
    return {"count": store.count()}


@router.get("/api/conversations", dependencies=[Depends(require_session_key)])
async def list_conversations():
    return [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at,
            "message_count": c.message_count,
            "last_response": c.last_response,
        }
        for c in store.list()
    ]


@router.post(
    "/api/conversations",
    response_model=ConversationResponse,
    dependencies=[Depends(require_session_key)],
)
async def create_conversation(body: CreateConversationRequest):
    if not get_settings().llm_api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY is not configured")
    conv = store.create(title=body.title)
    store.append_event(conv.id, "user", body.message)
    try:
        answer = _run_turn(conv.id, body.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc
    store.append_event(conv.id, "assistant", answer)
    updated = store.get(conv.id)
    assert updated is not None
    return ConversationResponse(
        id=updated.id,
        title=updated.title,
        created_at=updated.created_at,
        message_count=updated.message_count,
        last_response=updated.last_response,
        events=updated.events,
    )


@router.post("/api/conversations/stream", dependencies=[Depends(require_session_key)])
async def create_conversation_stream(body: CreateConversationRequest):
    if not get_settings().llm_api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY is not configured")
    conv = store.create(title=body.title)
    store.append_event(conv.id, "user", body.message)
    return StreamingResponse(
        _stream_conversation(conv.id, body.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/api/conversations/{conversation_id}",
    response_model=ConversationResponse,
    dependencies=[Depends(require_session_key)],
)
async def get_conversation(conversation_id: str):
    conv = store.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        message_count=conv.message_count,
        last_response=conv.last_response,
        events=conv.events,
    )


@router.post(
    "/api/conversations/{conversation_id}/messages",
    response_model=ConversationResponse,
    dependencies=[Depends(require_session_key)],
)
async def post_message(conversation_id: str, body: MessageRequest):
    conv = store.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    store.append_event(conversation_id, "user", body.message)
    try:
        answer = _run_turn(conversation_id, body.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc
    store.append_event(conversation_id, "assistant", answer)
    updated = store.get(conversation_id)
    assert updated is not None
    return ConversationResponse(
        id=updated.id,
        title=updated.title,
        created_at=updated.created_at,
        message_count=updated.message_count,
        last_response=updated.last_response,
        events=updated.events,
    )


@router.post(
    "/api/conversations/{conversation_id}/messages/stream",
    dependencies=[Depends(require_session_key)],
)
async def post_message_stream(conversation_id: str, body: MessageRequest):
    conv = store.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not get_settings().llm_api_key:
        raise HTTPException(status_code=503, detail="LLM_API_KEY is not configured")
    store.append_event(conversation_id, "user", body.message)
    return StreamingResponse(
        _stream_conversation(conversation_id, body.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
