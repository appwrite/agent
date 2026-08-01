"""Built-in remote MCP server catalog (no local persistence)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class McpServerDef:
    id: str
    name: str
    url: str
    description: str = ""
    builtin: bool = False


BUILTIN_SERVERS: tuple[McpServerDef, ...] = (
    McpServerDef(
        id="appwrite",
        name="Appwrite",
        url="https://mcp.appwrite.io/",
        description=(
            "Appwrite Cloud project tools and docs search "
            "(OAuth — no API key)."
        ),
        builtin=True,
    ),
)


def list_servers() -> list[McpServerDef]:
    return list(BUILTIN_SERVERS)


def get_server(server_id: str) -> McpServerDef | None:
    for server in BUILTIN_SERVERS:
        if server.id == server_id:
            return server
    return None


def resolve_server(
    *,
    server_id: str,
    name: str | None = None,
    url: str | None = None,
    description: str = "",
) -> McpServerDef:
    """Resolve a built-in server or an ad-hoc server definition from the request."""
    builtin = get_server(server_id)
    if builtin and not url:
        return builtin
    sid = server_id.strip().lower()
    if not _ID_RE.match(sid):
        raise ValueError(
            "id must be 2–64 chars, start with a letter, and use a-z, 0-9, _, -"
        )
    cleaned_url = (url or (builtin.url if builtin else "")).strip()
    # https for hosted MCP; http for local compose (e.g. http://localhost:8100).
    if not (
        cleaned_url.startswith("https://") or cleaned_url.startswith("http://")
    ):
        raise ValueError("url must be http:// or https://")
    return McpServerDef(
        id=sid,
        name=(name or (builtin.name if builtin else sid)).strip(),
        url=cleaned_url,
        description=description or (builtin.description if builtin else ""),
        builtin=bool(builtin and cleaned_url == builtin.url),
    )


def server_public_dict(server: McpServerDef) -> dict:
    return asdict(server)
