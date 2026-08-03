"""Agent prompts, routing schema, and LLM factory for the turn runner."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.config import Settings
from app.graph.content import content_to_text
from app.graph.skills import skill_index_text


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
5. Console UI requests (theme, navigate, open create dialogs, toasts) → appwrite
   or worker; they use the console tool.
6. After a successful subagent result that answers the user, FINISH with a clean
   final_answer (no [appwrite]/[researcher]/[worker] prefixes, no routing talk).
7. Never claim the assistant cannot help with Appwrite — the appwrite agent has
   official skills installed (and MCP when connected).
8. Never invent API shapes — prefer content from the appwrite agent / tools.
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
1) Live project actions (create/list/update/delete users, databases, buckets,
   functions, …) when MCP tools are available → use MCP only. Do NOT load
   appwrite_skill / CLI guides for those requests.
2) Load at most ONE skill per turn, and only when the user needs code samples
   for a specific SDK/CLI. Prefer appwrite-typescript / appwrite-python / etc.
   Never load appwrite-cli for in-console Cloud mutations. Never reload a skill
   you already loaded this turn (the tool will refuse with a short stub).
3) Call appwrite_skill name='list' only if you truly need the catalog.
4) If the skill is incomplete, browser_fetch https://appwrite.io/docs (or
   web_search site:appwrite.io) — do not invent APIs and do not load more skills.
5) Prefer modern Appwrite APIs (TablesDB / current SDK shapes from the skill).
6) MCP live ops: always appwrite_search_tools first; call only exact tool_name
   values from search (never invent names). Pass only required args unless the
   search/schema shows optionals. Omit permissions unless the user asks; when
   set, use Appwrite permission strings like read("any") / update("users"), not
   role:member or {{read/write}} objects. service_hints must be catalog service
   names (storage, organization, project) — not plurals like "projects".
   For list filters, each queries[] item MUST be a JSON *string* (not a dict,
   not Query.method(...)). Example:
   queries=['{{\"method\":\"greaterThanEqual\",\"attribute\":\"$createdAt\",\"values\":[\"2026-07-26T00:00:00.000Z\"]}}']
   Never pass objects like {{method, attribute, values}} and never SDK-style
   'greaterThanEqual(\"$createdAt\", \"…\")' or SQL-like 'createdAt>=…'.
   Use current_time first for relative dates ("this week"). Prefer '$createdAt'
   / '$updatedAt' (with the dollar sign).
7) Mutating tools are not idempotent. Do not retry the same create with the same
   concrete id. If the user did not give an id, use user_id/bucket_id/database_id=
   \"unique()\" (do not invent slug ids). When the user asks for N resources,
   call create N times (prefer sequential tool rounds over one parallel burst).
   Count only tool results that returned resource JSON — if a result says
   \"Blocked duplicate\" or \"did NOT run\", that create failed; never claim it
   succeeded. If create returns the resource JSON or says it is ready after
   already_exists recovery, report that one success — do not search/list just to
   restate that it exists. If a write returns an unclear/empty error, list/get
   once before any further create.
8) Keep tool use lean — context is limited. Do not dump multiple skills.
9) Never claim you lack Appwrite knowledge when skills or MCP tools are available.
10) Use sandbox_exec only as a stub note for project sandbox work.
11) After a successful MCP create/update/delete, call the console tool with a
   type=resource action so the Console can render a resource card (mutation +
   id + title). For Console UI requests (theme, navigate, open create dialog,
   toast, terminal, refresh lists) use console — it does not mutate resources.
12) After a successful MCP list/query (databases, users, buckets, functions,
   sites, teams, tables, files, …), call console with type=resource_list.
   Put each row in items[] with resourceId, title, href when known, and
   fields{{}} for filterable attributes (email, status, enabled, …). Keep the
   spoken/text answer short — do NOT restate the list as markdown bullets or
   tables; the Console renders the list UI from the protocol payload.

Return a concise, practical answer the supervisor can finish with.
"""

WORKER_PROMPT = """You are a worker subagent for Appwrite Cloud assistant.
Produce clear plans, API guidance, and next steps.
For deep Appwrite SDK/CLI questions, the appwrite expert is preferred — if you are
asked anyway, keep guidance high-level.
Use sandbox_exec only for work that must run in an isolated project sandbox.
Use the console tool for Console UI side-effects (set_theme, navigate, toast,
open_create, open_dialog, show_pane, toggle_terminal, scroll_to_card, refresh)
and for structured lists (resource_list) instead of markdown tables.
Do not invent tool results.
Return a concise result the supervisor can finish with.
"""


def _is_openai_reasoning_model(model: str) -> bool:
    """True for OpenAI reasoning models (gpt-5* except gpt-5-chat*, o-series).

    These models reject custom temperature and cannot use function tools on
    /v1/chat/completions unless reasoning_effort is none — use /v1/responses instead.
    """
    name = model.rsplit("/", 1)[-1].strip().lower()
    if name.startswith("gpt-5-chat"):
        return False
    if name.startswith("gpt-5"):
        return True
    if len(name) >= 2 and name[0] == "o" and name[1].isdigit():
        return True
    return False


def _model_allows_custom_temperature(model: str) -> bool:
    return not _is_openai_reasoning_model(model)


def _make_llm(settings: Settings, override: dict | None = None) -> ChatOpenAI:
    """Build the chat model. `override` (api_key/model/base_url/temperature)
    lets a single turn pin its own credential instead of the env defaults —
    only non-empty override fields win, everything else falls back to
    `settings`. Never log `override`: it may carry a live provider API key.
    """
    override = override or {}

    api_key = override.get("api_key") or settings.llm_api_key
    if not api_key:
        raise RuntimeError("LLM_API_KEY is required")

    model = override.get("model") or settings.chat_model
    kwargs: dict = {
        "model": model,
        "api_key": api_key,
    }

    if _model_allows_custom_temperature(model):
        temperature = override.get("temperature")
        kwargs["temperature"] = 0.2 if temperature is None else temperature
    else:
        # Function tools + default reasoning_effort 400 on chat.completions for
        # gpt-5.6 / o-series; Responses API is the supported path.
        kwargs["use_responses_api"] = True

    base_url = override.get("base_url") or settings.llm_base_url
    if base_url:
        kwargs["base_url"] = base_url

    return ChatOpenAI(**kwargs)


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return content_to_text(msg.content)
    return ""


def _strip_tags(text: str) -> str:
    for prefix in ("[researcher] ", "[appwrite] ", "[worker] "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text
