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

from app.config import get_settings
from app.graph.browser import browser_fetch_text, google_search_results, host_of

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
def google_search(
    query: Annotated[
        str,
        "Search query, e.g. 'Scottish Premiership Dundee United Rangers'",
    ],
    max_results: Annotated[
        int,
        "How many organic results to return (1-10, default 8)",
    ] = 8,
) -> str:
    """Search the web in a real browser — no Search API key.

    Opens Google like a normal user and reads result titles/URLs/snippets.
    If Google serves a CAPTCHA (common on cloud IPs), falls back to Bing then
    Brave in the same browser. Then browser_fetch promising result links.
    """
    settings = get_settings()
    if not settings.google_search_enabled:
        return (
            "google_search is disabled. Set GOOGLE_SEARCH_ENABLED=true to enable "
            "headless Google search."
        )
    q = " ".join((query or "").split())
    if not q:
        return "Error: query is empty"
    try:
        data = google_search_results(q, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        return f"Error running Google search: {exc}"

    results = data.get("results") or []
    if data.get("blocked") and not results:
        return (
            f"status={data.get('status')} final_url={data.get('final_url')} "
            f"source={data.get('source')}\n\n"
            "Search was blocked (bot/CAPTCHA). Try again later, or open a known "
            "URL with browser_fetch instead."
        )

    if not results:
        return (
            f"status={data.get('status')} final_url={data.get('final_url')} "
            f"source={data.get('source')}\n\n"
            "No organic results parsed from the results page."
        )

    lines = [
        f"query={q!r} status={data.get('status')} "
        f"source={data.get('source')} final_url={data.get('final_url')} "
        f"count={len(results)}",
        "",
    ]
    if data.get("note"):
        lines.append(str(data["note"]))
        lines.append("")
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


def build_tools() -> list:
    return [calculator, current_time, google_search, browser_fetch, sandbox_exec]
