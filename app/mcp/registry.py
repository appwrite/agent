"""Built-in and user-added remote MCP server definitions."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import get_settings

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class McpServerDef:
    id: str
    name: str
    url: str
    description: str = ""
    builtin: bool = False


# Default remote Appwrite MCP (OAuth, Streamable HTTP).
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


def _custom_path() -> Path:
    root = Path(get_settings().mcp_data_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    root.mkdir(parents=True, exist_ok=True)
    return root / "servers.json"


def _load_custom() -> list[McpServerDef]:
    path = _custom_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    items: list[McpServerDef] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        name = str(row.get("name") or sid).strip()
        url = str(row.get("url") or "").strip()
        if not sid or not url:
            continue
        items.append(
            McpServerDef(
                id=sid,
                name=name,
                url=url,
                description=str(row.get("description") or ""),
                builtin=False,
            )
        )
    return items


def _save_custom(servers: list[McpServerDef]) -> None:
    path = _custom_path()
    payload = [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "description": s.description,
        }
        for s in servers
        if not s.builtin
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_servers() -> list[McpServerDef]:
    by_id: dict[str, McpServerDef] = {s.id: s for s in BUILTIN_SERVERS}
    for custom in _load_custom():
        if custom.id not in by_id:
            by_id[custom.id] = custom
    return list(by_id.values())


def get_server(server_id: str) -> McpServerDef | None:
    for server in list_servers():
        if server.id == server_id:
            return server
    return None


def add_server(*, server_id: str, name: str, url: str, description: str = "") -> McpServerDef:
    sid = server_id.strip().lower()
    if not _ID_RE.match(sid):
        raise ValueError(
            "id must be 2–64 chars, start with a letter, and use a-z, 0-9, _, -"
        )
    if get_server(sid):
        raise ValueError(f"Server {sid!r} already exists")
    cleaned_url = url.strip()
    if not cleaned_url.startswith("https://"):
        raise ValueError("url must be https://")
    server = McpServerDef(
        id=sid,
        name=(name or sid).strip(),
        url=cleaned_url,
        description=(description or "").strip(),
        builtin=False,
    )
    customs = _load_custom()
    customs.append(server)
    _save_custom(customs)
    return server


def remove_server(server_id: str) -> None:
    server = get_server(server_id)
    if not server:
        raise ValueError("Server not found")
    if server.builtin:
        raise ValueError("Cannot remove built-in servers")
    customs = [s for s in _load_custom() if s.id != server_id]
    _save_custom(customs)


def server_public_dict(server: McpServerDef) -> dict:
    data = asdict(server)
    return data
