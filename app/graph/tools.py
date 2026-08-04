"""Safe tools only — no host shell, no arbitrary filesystem access."""

from __future__ import annotations

import ast
import ipaddress
import operator
import socket
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlparse

from langchain_core.tools import tool

from app.attachments import text_from_bytes
from app.config import get_settings
from app.graph.browser import browser_fetch_text, host_of, web_search_results
from app.graph.console import ConsoleProtocolError, emit_console_actions
from app.graph.skills import load_skill, resolve_skill_key, skill_index_text
from app.turn_context import (
    find_turn_attachment,
    mark_skill_loaded,
    skill_already_loaded,
)

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _eval_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_ast(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_ast(node.left), _eval_ast(node.right))
    raise ValueError("Only basic arithmetic is allowed")


def _is_blocked_host(hostname: str) -> bool:
    """Block obvious local/private targets (SSRF), not a public domain allowlist."""
    host = hostname.lower().strip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _validate_https_url(url: str) -> str | None:
    """Return an error string if invalid; otherwise None."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return "Error: only https:// URLs with a hostname are allowed"
    if _is_blocked_host(parsed.hostname):
        return f"Error: host '{parsed.hostname}' is not allowed (local/private)"
    return None


@tool
def calculator(expression: Annotated[str, "Arithmetic expression, e.g. (2+3)*4"]) -> str:
    """Evaluate a basic arithmetic expression safely (no code execution)."""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval_ast(tree.body))
    except Exception as exc:  # noqa: BLE001 — surface to the model
        return f"Error: {exc}"


@tool
def current_time() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


@tool
def browser_fetch(
    url: Annotated[
        str,
        "HTTPS page URL to open in headless Chromium. "
        "Use real pages, e.g. https://www.bbc.com/news, https://www.bbc.com/sport, "
        "or a specific article URL from search results / a previous Links section.",
    ],
) -> str:
    """Open any public HTTPS URL in headless Chromium and return visible text.

    Returns page text plus a Links list so you can open a specific story next.
    Call again with a different article URL for details. Same URL is cached briefly.
    """
    err = _validate_https_url(url)
    if err:
        return err

    try:
        status, final_url, text = browser_fetch_text(url)
    except Exception as exc:  # noqa: BLE001
        return f"Error fetching URL in browser: {exc}"

    final_host = host_of(final_url)
    if final_host and _is_blocked_host(final_host):
        return f"Error: redirect landed on blocked host '{final_host}'"

    text = (text or "").strip()
    if len(text) < 80:
        return (
            f"status={status} final_url={final_url} host={final_host}\n\n"
            "Page rendered little readable text (possible bot wall / empty shell). "
            f"Snippet:\n{text[:500]}"
        )

    return (
        f"status={status} final_url={final_url} host={final_host}\n\n"
        f"{text[:14000]}"
    )


@tool
def web_search(
    query: Annotated[
        str,
        "Search query, e.g. 'Scottish Premiership Dundee United Rangers'",
    ],
    max_results: Annotated[
        int,
        "How many organic results to return (1-10, default 8)",
    ] = 8,
) -> str:
    """Search the open web in a real browser — no Search API key.

    Returns organic result titles, URLs, and snippets. Follow promising links
    with browser_fetch when you need page content.
    """
    settings = get_settings()
    if not settings.web_search_enabled:
        return (
            "web_search is disabled. Set WEB_SEARCH_ENABLED=true to enable "
            "headless web search."
        )
    q = " ".join((query or "").split())
    if not q:
        return "Error: query is empty"
    try:
        data = web_search_results(q, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        return f"Error running web search: {exc}"

    results = data.get("results") or []
    if data.get("blocked") and not results:
        return (
            f"status={data.get('status')} count=0\n\n"
            "Search was blocked (bot/CAPTCHA). Try again later, or open a known "
            "URL with browser_fetch instead."
        )

    if not results:
        return (
            f"status={data.get('status')} count=0\n\n"
            "No organic results parsed from the results page."
        )

    lines = [
        f"query={q!r} status={data.get('status')} count={len(results)}",
        "",
    ]
    for i, item in enumerate(results, 1):
        title = item.get("title") or "(no title)"
        url = item.get("url") or ""
        snippet = item.get("snippet") or ""
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines).strip()


@tool
def appwrite_skill(
    name: Annotated[
        str,
        "Appwrite skill to load. Use 'list' for the catalog, or a skill id such as "
        "appwrite-typescript, appwrite-python, appwrite-cli, appwrite-dart, "
        "appwrite-flutter (via dart), appwrite-go, appwrite-kotlin, appwrite-php, "
        "appwrite-ruby, appwrite-rust, appwrite-swift, appwrite-dotnet. "
        "Bare language names like 'typescript' or 'python' also work.",
    ],
) -> str:
    """Load an installed Appwrite SDK/CLI skill guide (official agent-skills).

    For live Cloud project actions (create users, databases, buckets, …) prefer
    MCP tools — do not load skills. Load at most ONE language skill per turn
    when you need code samples; never reload the same skill.
    """
    key = resolve_skill_key(name)
    if key == "list":
        return skill_index_text()
    if skill_already_loaded(key):
        return (
            f"Skill {key!r} was already loaded earlier this turn. "
            "Reuse that content — do not call appwrite_skill again for it. "
            "For live project mutations use MCP tools (appwrite_search_tools → "
            "appwrite_call_tool), not CLI/SDK skill guides."
        )
    content = load_skill(key)
    mark_skill_loaded(key)
    return content


@tool
def read_attachment(
    attachment_id: Annotated[str, "Attachment id or filename from the current user turn"],
    max_chars: Annotated[
        int,
        "Max characters to return for text-like files (default 12000)",
    ] = 12_000,
) -> str:
    """Read an attachment from the current request (stateless; this turn only)."""
    att = find_turn_attachment(attachment_id)
    if not att:
        return (
            f"Unknown attachment id={attachment_id!r}. "
            "Only files attached on this turn are available."
        )
    data = att.get("_bytes")
    if not isinstance(data, (bytes, bytearray)):
        return f"Attachment {attachment_id!r} has no inline content on this request."
    body = text_from_bytes(
        bytes(data),
        max_chars=max(500, min(int(max_chars), 50_000)),
    )
    return (
        f"name={att.get('name')!r} mime={att.get('mime')} "
        f"size={att.get('size')}\n\n{body}"
    )


@tool
def sandbox_exec(
    instruction: Annotated[str, "What to run inside the project sandbox"],
) -> str:
    """Run work in an isolated Appwrite Function sandbox (not the agent host).

    Stub for Cloud: wire to per-project Function / MCP. Never runs on this container.
    """
    return (
        "sandbox_exec is not configured in this POC. "
        "In Cloud this will call a per-project Function sandbox MCP — "
        f"not the agent host. Requested: {instruction!r}"
    )


@tool
def console(
    actions: Annotated[
        str,
        "JSON array (or single object) of ConsoleAction envelopes for the "
        "Appwrite Console UI. Each object needs a `type` plus type-specific "
        "fields. Types: set_theme, navigate, open_create, open_dialog, toast, "
        "show_pane, toggle_terminal, scroll_to_card, resource, resource_list, "
        "chart, refresh. "
        "Examples: "
        '[{"type":"set_theme","theme":"dark"}] or '
        '[{"type":"resource_list","resourceType":"database","items":['
        '{"resourceId":"main","title":"Main","href":"/project/x/databases/main"}]}] or '
        '[{"type":"resource","mutation":"create","resourceType":"database",'
        '"resourceId":"db1","title":"Main DB"}] or '
        '[{"type":"chart","title":"Requests (last 24 hours)","unitLabel":"requests",'
        '"interval":"1h","metrics":[{"metric":"network.requests","points":'
        '[{"time":"2026-08-04T08:00:00Z","value":49}]}]}]. '
        "For usage charts: after usage_list_events/gauges, emit type=chart and "
        "pass metrics through. API request counts use metric network.requests "
        "(never bare requests). Always set interval for time series. "
        "Full contract: docs/console-protocol.md (protocol appwrite.console/v1).",
    ],
) -> str:
    """Post UI metadata to the Appwrite Console (theme, nav, lists, charts, resource cards).

    Does NOT create/update/delete Appwrite resources — use MCP for that, then call
    console with type=resource so the Console can render a card. For list/query
    results (databases, users, buckets, …) use type=resource_list — never dump the
    list as markdown. For usage_list_events / usage_list_gauges results use
    type=chart (metric network.requests for API requests; always set interval for
    time series). Unknown action types are rejected. The tool result is the
    canonical JSON envelope the Console parses from the tool_end output.
    """
    try:
        return emit_console_actions(actions)
    except ConsoleProtocolError as exc:
        return f"Error: invalid console actions — {exc}"


def build_tools() -> list:
    return [
        calculator,
        current_time,
        web_search,
        browser_fetch,
        appwrite_skill,
        read_attachment,
        sandbox_exec,
        console,
    ]


def build_appwrite_tools() -> list:
    """Tools for the Appwrite expert (skills + docs fetch + light helpers)."""
    return [
        appwrite_skill,
        browser_fetch,
        web_search,
        current_time,
        calculator,
        read_attachment,
        sandbox_exec,
        console,
    ]
