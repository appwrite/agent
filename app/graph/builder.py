"""LangGraph multi-agent: supervisor + researcher + appwrite + worker."""

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
from app.graph.skills import skill_index_text
from app.graph.tools import build_appwrite_tools, build_tools

_graph = None
MAX_HANDOFFS = 2
AGENTS = ("researcher", "appwrite", "worker")


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str
    handoffs: int


class Route(BaseModel):
    next: Literal["researcher", "appwrite", "worker", "FINISH"]
    reason: str = Field(
        default="",
        description="One short sentence explaining why this agent (or FINISH) was chosen.",
    )
    final_answer: str = Field(
        default="",
        description="Required when next is FINISH: the complete answer for the user.",
    )


SUPERVISOR_PROMPT = """You are the Appwrite Cloud assistant supervisor.
Route work to subagents. You do not run shell commands on the host.

Workers:
- appwrite: Appwrite expert — SDKs, CLI, auth, databases/tables, storage, functions,
  realtime, permissions, Cloud vs self-hosted. Uses installed Appwrite skills and
  connected MCP tools (live project operations when MCP is connected).
- researcher: gather facts, calculate, web search, open web pages (general web)
- worker: draft plans, structured answers, propose sandbox work via sandbox_exec

Rules:
1. Anything about Appwrite (APIs, SDKs, CLI, auth, DB, storage, functions, sites,
   messaging, permissions, self-hosting, Cloud) → route to appwrite first.
2. Live Appwrite project actions (list users, create tables, deploy, etc.) when
   MCP tools are available → appwrite.
3. General news / open-web research → researcher.
4. Generic planning / non-Appwrite coding plans → worker.
5. After a successful subagent result that answers the user, FINISH with a clean
   final_answer (no [appwrite]/[researcher]/[worker] prefixes, no routing talk).
6. Never claim the assistant cannot help with Appwrite — the appwrite agent has
   official skills installed (and MCP when connected).
7. Never invent API shapes — prefer content from the appwrite agent / tools.
"""

RESEARCHER_PROMPT = """You are a research subagent for Appwrite Cloud assistant.
Use only the provided tools. Never claim to have run host shell commands.
Never say you cannot access the web — use web_search and/or browser_fetch.
Prefer calculator / current_time / web_search / browser_fetch when useful.

Research patterns:
A) Open-web / find a story: web_search(query) → pick a URL → browser_fetch(url).
B) Known site section: browser_fetch a list page → follow ### Links → article.
Do not fetch the exact same URL twice. Different URLs (list → article) are good.
browser_fetch can open any public https URL.

For Appwrite product questions, the supervisor should have routed to the appwrite
expert — if you still get one, answer briefly and suggest Appwrite docs.
Return concise facts the supervisor can finish with.
"""

APPWRITE_EXPERT_PROMPT = f"""You are the Appwrite expert subagent for Appwrite Cloud assistant.
You specialize in Appwrite Cloud and self-hosted Appwrite: Auth, Databases/TablesDB,
Storage, Functions, Sites, Messaging, Realtime, Teams, permissions, CLI, and SDKs.

Installed skills (load the matching one with appwrite_skill before coding):
{skill_index_text()}

Workflow:
1) Call appwrite_skill with name='list' only if you need the catalog.
2) Load the best skill for the user's stack (e.g. appwrite-typescript, appwrite-python,
   appwrite-cli, appwrite-dart, appwrite-kotlin, appwrite-swift, appwrite-go,
   appwrite-php, appwrite-ruby, appwrite-rust, appwrite-dotnet).
3) Answer with accurate, copy-pasteable examples from that skill.
4) If the skill is incomplete, use browser_fetch on https://appwrite.io/docs
   (or web_search site:appwrite.io) — do not invent APIs.
5) Prefer modern Appwrite APIs (TablesDB / current SDK shapes from the skill).
6) When MCP tools are available (e.g. appwrite_get_context, appwrite_search_tools,
   appwrite_call_tool, appwrite_search_docs), use them for live project work and
   docs search — skills for code samples, MCP for the user's Cloud project.
7) Never claim you lack Appwrite knowledge when skills or MCP tools are available.
8) Use sandbox_exec only as a stub note for project sandbox work.

Return a concise, practical answer the supervisor can finish with.
"""

WORKER_PROMPT = """You are a worker subagent for Appwrite Cloud assistant.
Produce clear plans, API guidance, and next steps.
For deep Appwrite SDK/CLI questions, the appwrite expert is preferred — if you are
asked anyway, keep guidance high-level.
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


def _strip_tags(text: str) -> str:
    for prefix in ("[researcher] ", "[appwrite] ", "[worker] "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def build_graph(settings: Settings | None = None):
    settings = settings or get_settings()
    llm = _make_llm(settings)
    tools = build_tools()
    aw_tools = build_appwrite_tools()

    researcher = create_react_agent(llm, tools, prompt=RESEARCHER_PROMPT)
    appwrite = create_react_agent(llm, aw_tools, prompt=APPWRITE_EXPERT_PROMPT)
    worker = create_react_agent(llm, tools, prompt=WORKER_PROMPT)

    supervisor_llm = llm.with_structured_output(Route)

    def supervisor_node(state: AgentState) -> dict:
        handoffs = int(state.get("handoffs") or 0)
        if handoffs >= MAX_HANDOFFS:
            fallback = _strip_tags(
                _last_ai_text(state["messages"]) or "I could not complete the request."
            )
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
            answer = route.final_answer or _last_ai_text(state["messages"]) or "Done."
            return {"next": "FINISH", "messages": [AIMessage(content=_strip_tags(answer))]}
        return {"next": route.next}

    def _call_agent(agent, tag: str, state: AgentState, recursion: int) -> dict:
        result = agent.invoke(
            {"messages": state["messages"]},
            config={"recursion_limit": recursion},
        )
        last = result["messages"][-1]
        content = last.content if isinstance(last.content, str) else str(last.content)
        return {
            "messages": [AIMessage(content=f"[{tag}] {content}")],
            "handoffs": int(state.get("handoffs") or 0) + 1,
        }

    def call_researcher(state: AgentState) -> dict:
        return _call_agent(researcher, "researcher", state, 14)

    def call_appwrite(state: AgentState) -> dict:
        return _call_agent(appwrite, "appwrite", state, 14)

    def call_worker(state: AgentState) -> dict:
        return _call_agent(worker, "worker", state, 10)

    def route_next(
        state: AgentState,
    ) -> Literal["researcher", "appwrite", "worker", "__end__"]:
        nxt = state.get("next", "FINISH")
        if nxt == "FINISH":
            return "__end__"
        if nxt in AGENTS:
            return nxt  # type: ignore[return-value]
        return "__end__"

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("researcher", call_researcher)
    graph.add_node("appwrite", call_appwrite)
    graph.add_node("worker", call_worker)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_next,
        {
            "researcher": "researcher",
            "appwrite": "appwrite",
            "worker": "worker",
            "__end__": END,
        },
    )
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("appwrite", "supervisor")
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
