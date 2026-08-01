"""Async turn runner that emits UI-friendly stream events."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

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

# Enough for skill load → optional docs fetch → answer.
SUBAGENT_RECURSION_LIMIT = 14


def _strip_agent_prefix(text: str) -> str:
    return _strip_tags(text)


def _preview(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    if hasattr(value, "content"):
        value = value.content
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if len(text) > limit:
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


def _history_messages(history: Sequence[dict[str, str]] | None) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for item in history or []:
        role = (item.get("role") or "").lower()
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs


async def _stream_subagent(
    agent,
    agent_name: str,
    messages: list,
) -> AsyncIterator[dict[str, Any]]:
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

        if kind == "on_chat_model_start":
            current_text = ""
            streaming_answer = False
            yield {"type": "model_start", "agent": agent_name}

        elif kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            content = getattr(chunk, "content", None) if chunk is not None else None
            if isinstance(content, str) and content:
                if not streaming_answer:
                    streaming_answer = True
                    yield {"type": "answer_start", "agent": agent_name}
                current_text += content
                yield {"type": "token", "agent": agent_name, "content": content}

        elif kind == "on_chat_model_end":
            output = data.get("output")
            calls = getattr(output, "tool_calls", None) or []
            content = getattr(output, "content", "") if output is not None else ""
            if isinstance(content, str) and content and not calls:
                last_text = content
            # Tool-bound turns are not the user-facing answer — clear the draft.
            if calls:
                yield {"type": "answer_reset", "agent": agent_name}

        elif kind == "on_tool_start":
            tool_calls += 1
            yield {
                "type": "tool_start",
                "agent": agent_name,
                "tool": name,
                "input": _preview(data.get("input"), 600),
            }

        elif kind == "on_tool_end":
            yield {
                "type": "tool_end",
                "agent": agent_name,
                "tool": name,
                "output": _preview(data.get("output"), 800),
            }

    final = last_text or current_text
    yield {
        "type": "subagent_end",
        "agent": agent_name,
        "content": final,
        "tool_calls": tool_calls,
    }


async def _finish_with_answer(
    answer: str,
    *,
    reason: str,
) -> AsyncIterator[dict[str, Any]]:
    clean = _strip_agent_prefix(answer).strip() or "Done."
    yield {"type": "route", "next": "FINISH", "reason": reason}
    # Final answer is authoritative — do not soft-stream a second rewrite.
    yield {"type": "done", "answer": clean}


async def stream_turn(
    message: str,
    history: Sequence[dict[str, str]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one user turn and yield stream events (caller owns persistence)."""
    llm = _make_llm(get_settings())
    tools = build_tools()
    aw_tools = build_appwrite_tools()
    researcher = create_react_agent(llm, tools, prompt=RESEARCHER_PROMPT)
    appwrite = create_react_agent(llm, aw_tools, prompt=APPWRITE_EXPERT_PROMPT)
    worker = create_react_agent(llm, tools, prompt=WORKER_PROMPT)
    supervisor_llm = llm.with_structured_output(Route)

    agents = {
        "researcher": researcher,
        "appwrite": appwrite,
        "worker": worker,
    }

    messages: list[BaseMessage] = [
        *_history_messages(history),
        HumanMessage(content=message),
    ]
    handoffs = 0
    # At most one pass per subagent per turn — stops rewrite loops.
    used_agents: set[str] = set()

    yield {"type": "status", "message": "Supervisor is routing…"}

    while True:
        yield {"type": "subagent_start", "agent": "supervisor"}
        msgs = [SystemMessage(content=SUPERVISOR_PROMPT), *messages]
        if handoffs > 0:
            msgs.append(
                SystemMessage(
                    content=(
                        "A subagent already produced a result. "
                        "You MUST choose FINISH now and put the user-facing answer "
                        "in final_answer. Do not route to researcher, appwrite, or "
                        "worker again unless the prior result was clearly an error."
                    )
                )
            )
        route: Route = await supervisor_llm.ainvoke(msgs)
        yield {
            "type": "route",
            "next": route.next,
            "agent": "supervisor",
        }
        yield {"type": "subagent_end", "agent": "supervisor"}

        if route.next == "FINISH":
            answer = route.final_answer or _last_ai_text(messages) or "Done."
            async for event in _finish_with_answer(answer, reason="supervisor_finish"):
                yield event
            return

        agent_name = route.next
        if agent_name not in agents:
            async for event in _finish_with_answer(
                _last_ai_text(messages) or "Done.",
                reason="unknown_agent",
            ):
                yield event
            return

        if agent_name in used_agents:
            async for event in _finish_with_answer(
                _last_ai_text(messages) or "Done.",
                reason="duplicate_handoff_blocked",
            ):
                yield event
            return

        agent = agents[agent_name]
        used_agents.add(agent_name)
        sub_content = ""
        async for event in _stream_subagent(agent, agent_name, messages):
            if event["type"] == "subagent_end":
                sub_content = event.get("content") or ""
            yield event

        messages.append(AIMessage(content=f"[{agent_name}] {sub_content}"))
        handoffs += 1
        yield {"type": "status", "message": "Back to supervisor…"}

        if sub_content and not _looks_like_failure(sub_content):
            polish_msgs = [
                SystemMessage(content=SUPERVISOR_PROMPT),
                *messages,
                SystemMessage(
                    content=(
                        "The subagent succeeded. Choose FINISH and rewrite their "
                        "result into a clean final_answer for the user. "
                        "Do not invent facts beyond the subagent result."
                    )
                ),
            ]
            polish: Route = await supervisor_llm.ainvoke(polish_msgs)
            answer = (
                polish.final_answer
                if polish.next == "FINISH" and polish.final_answer.strip()
                else sub_content
            )
            async for event in _finish_with_answer(answer, reason="subagent_complete"):
                yield event
            return
