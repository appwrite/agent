"""LangGraph multi-agent: supervisor + researcher + worker subagents."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.graph.tools import build_tools

_graph = None
MAX_HANDOFFS = 2


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str
    handoffs: int


class Route(BaseModel):
    next: Literal["researcher", "worker", "FINISH"]
    final_answer: str = Field(
        default="",
        description="Required when next is FINISH: the complete answer for the user.",
    )


SUPERVISOR_PROMPT = """You are the Appwrite Cloud assistant supervisor.
Route work to subagents. You do not run shell commands on the host.

Workers:
- researcher: gather facts, calculate, Google search, open web pages, answer questions
- worker: draft plans, write structured answers, propose sandbox work via sandbox_exec

Rules:
1. News / search / "what's happening" / "tell me more about that story" → researcher.
2. After a successful subagent result that answers the user, FINISH with a clean
   final_answer (no [researcher]/[worker] prefixes, no routing talk).
3. Never claim the assistant cannot browse/search — researcher has google_search
   and browser_fetch.
4. Only re-route if the prior result was clearly an error/empty.
5. Never invent headlines — only use content from subagent/tool results.
"""

RESEARCHER_PROMPT = """You are a research subagent for Appwrite Cloud assistant.
Use only the provided tools. Never claim to have run host shell commands.
Never say you cannot access the web — use google_search and/or browser_fetch.
Prefer calculator / current_time / google_search / browser_fetch when useful.

Research patterns:
A) Open-web / find a story: google_search(query) → pick a URL → browser_fetch(url).
B) Known site section: browser_fetch a list page → follow ### Links → article.
Do not fetch the exact same URL twice. Different URLs (list → article) are good.
browser_fetch can open any public https URL.

For "latest headlines", a list page or google_search may be enough.
For "more about this story", open the article page, then summarize.
Return concise facts the supervisor can finish with.
"""

WORKER_PROMPT = """You are a worker subagent for Appwrite Cloud assistant.
Produce clear plans, API guidance, and next steps.
Use sandbox_exec only for work that must run in an isolated project sandbox.
Do not invent tool results.
Return a concise result the supervisor can finish with.
"""


def _make_llm(settings: Settings) -> ChatOpenAI:
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is required")
    kwargs: dict = {
        "model": settings.chat_model,
        "api_key": settings.llm_api_key,
        "temperature": 0.2,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    return ChatOpenAI(**kwargs)


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def build_graph(settings: Settings | None = None):
    settings = settings or get_settings()
    llm = _make_llm(settings)
    tools = build_tools()

    researcher = create_react_agent(llm, tools, prompt=RESEARCHER_PROMPT)
    worker = create_react_agent(llm, tools, prompt=WORKER_PROMPT)

    members = ["researcher", "worker"]
    supervisor_llm = llm.with_structured_output(Route)

    def supervisor_node(state: AgentState) -> dict:
        handoffs = int(state.get("handoffs") or 0)
        if handoffs >= MAX_HANDOFFS:
            fallback = _last_ai_text(state["messages"]) or "I could not complete the request."
            # Strip subagent tags for the user-facing answer.
            fallback = fallback.removeprefix("[researcher] ").removeprefix("[worker] ")
            return {
                "next": "FINISH",
                "messages": [AIMessage(content=fallback)],
            }

        msgs = [SystemMessage(content=SUPERVISOR_PROMPT), *state["messages"]]
        if handoffs > 0:
            msgs.append(
                SystemMessage(
                    content=(
                        f"Handoffs so far: {handoffs}/{MAX_HANDOFFS}. "
                        "If a subagent already answered, choose FINISH now."
                    )
                )
            )

        route: Route = supervisor_llm.invoke(msgs)
        if route.next == "FINISH" or handoffs > 0:
            # After any subagent pass, finish — do not re-handoff and re-fetch.
            answer = route.final_answer or _last_ai_text(state["messages"]) or "Done."
            answer = answer.removeprefix("[researcher] ").removeprefix("[worker] ")
            return {"next": "FINISH", "messages": [AIMessage(content=answer)]}
        return {"next": route.next}

    def call_researcher(state: AgentState) -> dict:
        result = researcher.invoke(
            {"messages": state["messages"]},
            config={"recursion_limit": 14},
        )
        last = result["messages"][-1]
        content = last.content if isinstance(last.content, str) else str(last.content)
        return {
            "messages": [AIMessage(content=f"[researcher] {content}")],
            "handoffs": int(state.get("handoffs") or 0) + 1,
        }

    def call_worker(state: AgentState) -> dict:
        result = worker.invoke(
            {"messages": state["messages"]},
            config={"recursion_limit": 10},
        )
        last = result["messages"][-1]
        content = last.content if isinstance(last.content, str) else str(last.content)
        return {
            "messages": [AIMessage(content=f"[worker] {content}")],
            "handoffs": int(state.get("handoffs") or 0) + 1,
        }

    def route_next(state: AgentState) -> Literal["researcher", "worker", "__end__"]:
        nxt = state.get("next", "FINISH")
        if nxt == "FINISH":
            return "__end__"
        if nxt in members:
            return nxt  # type: ignore[return-value]
        return "__end__"

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("researcher", call_researcher)
    graph.add_node("worker", call_worker)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_next,
        {"researcher": "researcher", "worker": "worker", "__end__": END},
    )
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("worker", "supervisor")

    return graph.compile(checkpointer=MemorySaver())


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_graph() -> None:
    global _graph
    _graph = None
