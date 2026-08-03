"""Safe, non-secret snapshot of agent runtime settings for the UI."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.graph.builder import (
    APPWRITE_EXPERT_PROMPT,
    MAX_HANDOFFS,
    RESEARCHER_PROMPT,
    SUPERVISOR_PROMPT,
    WORKER_PROMPT,
    _model_allows_custom_temperature,
)
from app.graph.browser import CACHE_TTL_S
from app.graph.skills import list_skill_meta
from app.graph.stream import SUBAGENT_RECURSION_LIMIT
from app.graph.tools import build_appwrite_tools, build_tools
from app.mcp import get_mcp_manager

HISTORY_WINDOW = 12
BROWSER_FETCH_TIMEOUT_MS = 35_000
BROWSER_FETCH_TEXT_LIMIT = 14_000
LLM_TEMPERATURE = 0.2


def agent_settings_snapshot() -> dict[str, Any]:
    settings = get_settings()
    tools = []
    for t in build_tools():
        tools.append(
            {
                "name": t.name,
                "description": (t.description or "").strip(),
            }
        )

    aw_tools = [
        {"name": t.name, "description": (t.description or "").strip()}
        for t in build_appwrite_tools()
    ]

    skills = [
        {"name": row["name"], "description": row["description"]}
        for row in list_skill_meta()
    ]

    return {
        "llm": {
            "model": settings.llm_model,
            "chat_model": settings.chat_model,
            "base_url": settings.llm_base_url or None,
            "api_key_configured": bool(settings.llm_api_key),
            # Reasoning models (gpt-5*, o-series) only accept the API default; we omit it.
            "temperature": (
                LLM_TEMPERATURE
                if _model_allows_custom_temperature(settings.chat_model)
                else None
            ),
        },
        "auth": {
            "session_api_key_configured": bool(settings.assistant_api_key),
            "header": "X-Session-API-Key",
        },
        "server": {
            "host": settings.host,
            "port": settings.port,
        },
        "browser_fetch": {
            "enabled": True,
            "domain_limits": "none (public https only; local/private hosts blocked)",
            "timeout_ms": BROWSER_FETCH_TIMEOUT_MS,
            "text_limit": BROWSER_FETCH_TEXT_LIMIT,
            "cache_ttl_seconds": int(CACHE_TTL_S),
            "engine": "playwright/chromium",
        },
        "web_search": {
            "enabled": bool(settings.web_search_enabled),
            "engine": "headless browser (no API key)",
            "api_key_required": False,
        },
        "appwrite_skills": {
            "count": len(skills),
            "skills": skills,
            "loader_tool": "appwrite_skill",
        },
        "mcp": {
            "note": (
                "OAuth is handled entirely by the client. Pass mcp_connections "
                "(url + tokens + client_info) on each POST /api/turn."
            ),
            "suggested_servers": get_mcp_manager().suggested_servers(),
        },
        "runtime": {
            "max_handoffs": MAX_HANDOFFS,
            "subagent_recursion_limit": SUBAGENT_RECURSION_LIMIT,
            "history_window": HISTORY_WINDOW,
            "graph": "supervisor → researcher | appwrite | worker → FINISH",
        },
        "tools": tools,
        "appwrite_tools": aw_tools,
        "agents": [
            {
                "name": "supervisor",
                "role": "Routes work and produces the final user-facing answer",
                "prompt": SUPERVISOR_PROMPT.strip(),
            },
            {
                "name": "appwrite",
                "role": "Appwrite expert — SDKs, CLI, Cloud (loads installed skills)",
                "prompt": APPWRITE_EXPERT_PROMPT.strip(),
            },
            {
                "name": "researcher",
                "role": "Facts, calculation, web search, browser fetch",
                "prompt": RESEARCHER_PROMPT.strip(),
            },
            {
                "name": "worker",
                "role": "Plans, structured answers, sandbox_exec stub",
                "prompt": WORKER_PROMPT.strip(),
            },
        ],
        "env": {
            "LLM_MODEL": settings.llm_model,
            "LLM_BASE_URL": settings.llm_base_url or "",
            "WEB_SEARCH_ENABLED": "true" if settings.web_search_enabled else "false",
            "HOST": settings.host,
            "PORT": str(settings.port),
            "LLM_API_KEY": "••••••" if settings.llm_api_key else "",
            "ASSISTANT_API_KEY": "••••••" if settings.assistant_api_key else "",
        },
    }
