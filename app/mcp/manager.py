"""Remote MCP client — credentials supplied per turn; no OAuth or durable state."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from app.mcp.registry import McpServerDef, list_servers, resolve_server
from app.mcp.storage import MemoryTokenStorage

logger = logging.getLogger(__name__)

# Placeholder redirect for the MCP OAuth client metadata when only using
# pre-supplied tokens (refresh). Real OAuth is owned by the client/proxy.
_PLACEHOLDER_REDIRECT = "http://127.0.0.1/oauth/callback"


class McpManager:
    def suggested_servers(self) -> list[dict[str, Any]]:
        """Static built-in MCP URLs (hints only — no connection status)."""
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

    def _oauth_provider(
        self,
        server: McpServerDef,
        *,
        storage: MemoryTokenStorage,
    ) -> OAuthClientProvider:
        async def _no_redirect(url: str) -> None:
            raise RuntimeError(
                f"MCP server {server.id!r} needs re-authentication "
                "(OAuth is handled by the client/proxy)."
            )

        async def _no_callback() -> tuple[str, str | None]:
            raise RuntimeError(
                f"MCP server {server.id!r} needs re-authentication "
                "(OAuth is handled by the client/proxy)."
            )

        return OAuthClientProvider(
            server_url=server.url,
            client_metadata=OAuthClientMetadata(
                client_name="Appwrite Assistant",
                redirect_uris=[AnyUrl(_PLACEHOLDER_REDIRECT)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=storage,
            redirect_handler=_no_redirect,
            callback_handler=_no_callback,
        )

    async def _list_tools_for(
        self, server: McpServerDef, auth: OAuthClientProvider
    ) -> list[BaseTool]:
        client = MultiServerMCPClient(
            {
                server.id: {
                    "url": server.url,
                    "transport": "streamable_http",
                    "auth": auth,
                }
            }
        )
        return list(await client.get_tools())

    async def tools_from_connections(
        self, connections: list[dict[str, Any]] | None
    ) -> tuple[list[BaseTool], list[dict[str, Any]]]:
        """Build MCP tools from credentials supplied on the turn request.

        Returns (tools, updated_credentials) so refreshed tokens can be persisted
        by the caller/proxy.
        """
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
            if not storage.has_tokens():
                continue

            auth = self._oauth_provider(server, storage=storage)
            try:
                tools.extend(await self._list_tools_for(server, auth))
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
