"""Async turn runner that emits UI-friendly stream events (stateless)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.attachments import build_human_content, normalize_attachments
from app.config import get_settings
from app.graph.builder import (
    APPWRITE_EXPERT_PROMPT,
    RESEARCHER_PROMPT,
    SUPERVISOR_PROMPT,
    WORKER_PROMPT,
    Route,
    _last_ai_text,
    _make_llm,
    _strip_tags,
)
from app.graph.tools import build_appwrite_tools, build_tools
from app.mcp import get_mcp_manager
from app.turn_context import set_turn_attachments

# MCP workflows often need search → call → retry; keep headroom above the
# default react-agent budget so a few bad args do not end the turn early.
SUBAGENT_RECURSION_LIMIT = 40


def _strip_agent_prefix(text: str) -> str:
    return _strip_tags(text)


def _preview(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if hasattr(value, "content"):
        value = value.content
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if limit > 0 and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _looks_like_failure(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    failure_prefixes = (
        "error",
        "i could not",
        "i can't",
        "i cannot",
        "unable to",
        "failed to",
        "browser_fetch is disabled",
        "sandbox_exec is not configured",
    )
    return any(lowered.startswith(p) for p in failure_prefixes)


def _history_messages(history: Sequence[dict[str, Any]] | None) -> list[BaseMessage]:
    """Build LangChain messages from client/proxy-supplied prior turns (text only)."""
    msgs: list[BaseMessage] = []
    for item in history or []:
        role = (item.get("role") or "").lower()
        content = (item.get("content") or "").strip()
        if role == "user":
            if not content:
                continue
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            if not content:
                continue
            msgs.append(AIMessage(content=content))
    return msgs


async def _stream_subagent(
    agent,
    agent_name: str,
    messages: list,
) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    tool_input_limit = settings.stream_tool_input_chars
    tool_output_limit = settings.stream_tool_output_chars

    yield {"type": "subagent_start", "agent": agent_name}

    last_text = ""
    current_text = ""
    streaming_answer = False
    tool_calls = 0

    async for event in agent.astream_events(
        {"messages": messages},
        version="v2",
        config={"recursion_limit": SUBAGENT_RECURSION_LIMIT},
    ):
        kind = event.get("event")
        name = event.get("name") or ""
        data = event.get("data") or {}

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            piece = getattr(chunk, "content", None) if chunk is not None else None
            if isinstance(piece, list):
                piece = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in piece
                )
            if isinstance(piece, str) and piece:
                if not streaming_answer:
                    streaming_answer = True
                    yield {"type": "answer_start", "agent": agent_name}
                current_text += piece
                yield {
                    "type": "token",
                    "agent": agent_name,
                    "content": piece,
                }

        elif kind == "on_tool_start":
            tool_calls += 1
            if streaming_answer:
                streaming_answer = False
                current_text = ""
            yield {
                "type": "tool_start",
                "agent": agent_name,
                "tool": name,
                "input": _preview(data.get("input"), tool_input_limit),
            }

        elif kind == "on_tool_end":
            yield {
                "type": "tool_end",
                "agent": agent_name,
                "tool": name,
                "output": _preview(data.get("output"), tool_output_limit),
            }

        elif kind == "on_tool_error":
            # Unhandled tool exceptions skip on_tool_end; surface them so the
            # UI/worker do not leave the call stuck in "running".
            err = data.get("error")
            detail = _preview(err, tool_output_limit) or "Tool execution failed"
            yield {
                "type": "tool_end",
                "agent": agent_name,
                "tool": name,
                "output": f"Error: {detail}",
                "failed": True,
            }

        elif kind == "on_chain_end" and name == "LangGraph":
            output = data.get("output") or {}
            messages_out = output.get("messages") if isinstance(output, dict) else None
            if messages_out:
                last_text = _strip_agent_prefix(_last_ai_text(messages_out))

    final = _strip_agent_prefix(last_text or current_text)
    if final and not streaming_answer:
        yield {"type": "answer_start", "agent": agent_name}
        yield {"type": "token", "agent": agent_name, "content": final}

    yield {
        "type": "subagent_end",
        "agent": agent_name,
        "summary": _preview(final, 2000),
        "tool_calls": tool_calls,
        "failed": _looks_like_failure(final),
    }
    yield {"type": "final", "agent": agent_name, "content": final}


async def run_turn_stream(
    *,
    message: str,
    history: Sequence[dict[str, Any]] | None = None,
    attachments: Sequence[dict[str, Any]] | None = None,
    mcp_connections: Sequence[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one agent turn. All conversation / MCP / file context comes from the request."""
    settings = get_settings()
    llm = _make_llm(settings)
    normalized = normalize_attachments(attachments)
    set_turn_attachments(normalized)

    mcp = get_mcp_manager()
    mcp_tools, refreshed_creds = await mcp.tools_from_connections(mcp_connections)
    if refreshed_creds:
        yield {
            "type": "mcp_credentials",
            "credentials": refreshed_creds,
        }

    tools = [*build_tools(), *mcp_tools]
    appwrite_tools = [*build_appwrite_tools(), *mcp_tools]

    router = llm.with_structured_output(Route)
    researcher = create_react_agent(llm, tools=tools, prompt=RESEARCHER_PROMPT)
    worker = create_react_agent(llm, tools=tools, prompt=WORKER_PROMPT)
    appwrite = create_react_agent(llm, tools=appwrite_tools, prompt=APPWRITE_EXPERT_PROMPT)

    prior = _history_messages(history)
    human = HumanMessage(content=build_human_content(message, normalized))
    conversation = [*prior, human]

    yield {"type": "status", "message": "Routing request…"}
    decision = await router.ainvoke(
        [
            SystemMessage(content=SUPERVISOR_PROMPT),
            *conversation,
        ]
    )
    next_agent = decision.next
    reason = (decision.reason or "").strip() or f"Routed to {next_agent}"
    yield {
        "type": "route",
        "agent": next_agent,
        "next": next_agent,
        "reason": reason,
    }

    # Direct finish (e.g. trivial follow-up) — no subagent needed.
    if next_agent == "FINISH":
        final_text = _strip_agent_prefix((decision.final_answer or "").strip())
        if final_text:
            yield {"type": "answer_start", "agent": "supervisor"}
            yield {"type": "token", "agent": "supervisor", "content": final_text}
        yield {"type": "done", "content": final_text, "answer": final_text}
        return

    agent_map = {
        "researcher": researcher,
        "worker": worker,
        "appwrite": appwrite,
    }
    agent = agent_map.get(next_agent)
    if agent is None:
        yield {
            "type": "error",
            "detail": f"Unknown route {next_agent!r}",
        }
        return

    messages = [SystemMessage(content=f"Task focus: {reason}"), *conversation]

    final_text = ""
    async for event in _stream_subagent(agent, next_agent, messages):
        if event.get("type") == "final":
            final_text = str(event.get("content") or "")
            continue
        yield event

    if (
        next_agent in {"researcher", "appwrite"}
        and final_text
        and _looks_like_failure(final_text)
    ):
        yield {
            "type": "status",
            "message": "Primary agent stalled — falling back to worker…",
        }
        yield {
            "type": "route",
            "agent": "worker",
            "next": "worker",
            "reason": f"Fallback after {next_agent} could not complete the task",
        }
        fallback_messages = [
            SystemMessage(
                content=(
                    f"The {next_agent} agent failed with:\n{final_text}\n\n"
                    "Complete the user's request with the tools you have."
                )
            ),
            *conversation,
        ]
        async for event in _stream_subagent(worker, "worker", fallback_messages):
            if event.get("type") == "final":
                final_text = str(event.get("content") or "")
                continue
            yield event

    yield {"type": "done", "content": final_text, "answer": final_text}
