"""Remote MCP client — credentials supplied per turn; no OAuth on the engine."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.mcp.registry import McpServerDef, list_servers, resolve_server
from app.mcp.storage import MemoryTokenStorage
from app.mcp.write_guard import wrap_mcp_tool

logger = logging.getLogger(__name__)


class McpManager:
    def suggested_servers(self) -> list[dict[str, Any]]:
        """Static built-in MCP URLs (hints only — no connection state)."""
        return [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "description": s.description,
                "builtin": s.builtin,
            }
            for s in list_servers()
        ]

    async def _list_tools_for(
        self, server: McpServerDef, *, access_token: str
    ) -> list[BaseTool]:
        # Static Bearer only — never OAuthClientProvider. mcp<=1.13.x's
        # async_auth_flow always re-yields the request (even on 200), so one
        # tools/call becomes two Appwrite writes (201 then 409).
        client = MultiServerMCPClient(
            {
                server.id: {
                    "url": server.url,
                    "transport": "streamable_http",
                    "headers": {"Authorization": f"Bearer {access_token}"},
                }
            }
        )
        return list(await client.get_tools())

    async def tools_from_connections(
        self, connections: list[dict[str, Any]] | None
    ) -> tuple[list[BaseTool], list[dict[str, Any]]]:
        """Build MCP tools from credentials supplied on the turn request."""
        tools: list[BaseTool] = []
        updated: list[dict[str, Any]] = []
        for conn in connections or []:
            sid = str(conn.get("id") or "").strip()
            if not sid:
                continue
            try:
                server = resolve_server(
                    server_id=sid,
                    name=conn.get("name"),
                    url=conn.get("url"),
                    description=str(conn.get("description") or ""),
                )
            except ValueError:
                logger.warning("Skipping invalid MCP connection %r", sid)
                continue

            storage = MemoryTokenStorage(
                tokens=conn.get("tokens"),
                client_info=conn.get("client_info"),
            )
            access_token = storage.access_token()
            if not access_token:
                continue

            try:
                loaded = await self._list_tools_for(
                    server, access_token=access_token
                )
                # Error→ToolMessage + turn-scoped create dedupe (see write_guard).
                tools.extend(wrap_mcp_tool(tool) for tool in loaded)
                exported = storage.export()
                updated.append(
                    {
                        "id": server.id,
                        "name": server.name,
                        "url": server.url,
                        "description": server.description,
                        **exported,
                    }
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to load tools from MCP server %s", sid)
        return tools, updated


_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager
