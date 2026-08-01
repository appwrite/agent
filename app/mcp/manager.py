"""Generic remote MCP client manager with OAuth 2.1 + PKCE."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from app.config import get_settings
from app.mcp.registry import McpServerDef, get_server, list_servers
from app.mcp.storage import FileTokenStorage

logger = logging.getLogger(__name__)


@dataclass
class _PendingOAuth:
    server_id: str
    code_future: asyncio.Future
    auth_url: str | None = None
    auth_ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    error: str | None = None


class McpManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending_by_state: dict[str, _PendingOAuth] = {}
        self._pending_by_server: dict[str, _PendingOAuth] = {}
        self._tool_cache: dict[str, list[str]] = {}

    def _data_root(self) -> Path:
        root = Path(get_settings().mcp_data_dir)
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _storage(self, server_id: str) -> FileTokenStorage:
        return FileTokenStorage(self._data_root() / server_id)

    def redirect_uri(self) -> str:
        base = get_settings().mcp_oauth_redirect_base.rstrip("/")
        return f"{base}/api/mcp/oauth/callback"

    def status_snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for server in list_servers():
            storage = self._storage(server.id)
            connected = storage.has_tokens()
            pending = server.id in self._pending_by_server
            rows.append(
                {
                    "id": server.id,
                    "name": server.name,
                    "url": server.url,
                    "description": server.description,
                    "builtin": server.builtin,
                    "status": (
                        "connecting"
                        if pending
                        else "connected"
                        if connected
                        else "disconnected"
                    ),
                    "tools": self._tool_cache.get(server.id, []) if connected else [],
                }
            )
        return rows

    def _oauth_provider(
        self,
        server: McpServerDef,
        *,
        storage: FileTokenStorage,
        redirect_handler,
        callback_handler,
    ) -> OAuthClientProvider:
        return OAuthClientProvider(
            server_url=server.url,
            client_metadata=OAuthClientMetadata(
                client_name=get_settings().mcp_oauth_client_name,
                redirect_uris=[AnyUrl(self.redirect_uri())],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

    def _connection(self, server: McpServerDef, auth: OAuthClientProvider) -> dict:
        return {
            "url": server.url,
            "transport": "streamable_http",
            "auth": auth,
        }

    async def _list_tools_for(
        self, server: McpServerDef, auth: OAuthClientProvider
    ) -> list[BaseTool]:
        client = MultiServerMCPClient({server.id: self._connection(server, auth)})
        tools = await client.get_tools()
        self._tool_cache[server.id] = [t.name for t in tools]
        return list(tools)

    async def get_connected_tools(self) -> list[BaseTool]:
        """LangChain tools from every MCP server that has stored tokens."""
        tools: list[BaseTool] = []
        for server in list_servers():
            storage = self._storage(server.id)
            if not storage.has_tokens():
                continue
            sid = server.id

            async def _no_redirect(url: str, _sid: str = sid) -> None:
                raise RuntimeError(
                    f"MCP server {_sid!r} needs re-authentication. "
                    "Open Agent settings → Connections and connect again."
                )

            async def _no_callback(_sid: str = sid) -> tuple[str, str | None]:
                raise RuntimeError(
                    f"MCP server {_sid!r} needs re-authentication."
                )

            auth = self._oauth_provider(
                server,
                storage=storage,
                redirect_handler=_no_redirect,
                callback_handler=_no_callback,
            )
            try:
                tools.extend(await self._list_tools_for(server, auth))
            except Exception:  # noqa: BLE001
                logger.exception("Failed to load tools from MCP server %s", server.id)
        return tools

    async def start_connect(self, server_id: str) -> dict[str, str]:
        server = get_server(server_id)
        if not server:
            raise KeyError("Server not found")

        async with self._lock:
            existing = self._pending_by_server.get(server_id)
            if existing and existing.auth_url:
                return {
                    "server_id": server_id,
                    "authorization_url": existing.auth_url,
                    "redirect_uri": self.redirect_uri(),
                }

            loop = asyncio.get_running_loop()
            pending = _PendingOAuth(
                server_id=server_id,
                code_future=loop.create_future(),
            )
            self._pending_by_server[server_id] = pending
            pending.task = asyncio.create_task(
                self._run_connect(server, pending),
                name=f"mcp-oauth-{server_id}",
            )

        try:
            await asyncio.wait_for(pending.auth_ready.wait(), timeout=90)
        except TimeoutError as exc:
            await self._cancel_pending(server_id)
            raise TimeoutError("Timed out waiting for OAuth authorization URL") from exc

        if pending.error:
            await self._cancel_pending(server_id)
            raise RuntimeError(pending.error)
        if not pending.auth_url:
            # Tokens already present — connect finished without browser.
            await self._wait_task(pending)
            self._pending_by_server.pop(server_id, None)
            return {
                "server_id": server_id,
                "authorization_url": "",
                "redirect_uri": self.redirect_uri(),
                "status": "connected",
            }

        return {
            "server_id": server_id,
            "authorization_url": pending.auth_url,
            "redirect_uri": self.redirect_uri(),
        }

    async def _run_connect(self, server: McpServerDef, pending: _PendingOAuth) -> None:
        storage = self._storage(server.id)

        async def redirect_handler(url: str) -> None:
            qs = parse_qs(urlparse(url).query)
            state = (qs.get("state") or [None])[0]
            if state:
                self._pending_by_state[state] = pending
            pending.auth_url = url
            pending.auth_ready.set()

        async def callback_handler() -> tuple[str, str | None]:
            return await pending.code_future

        # If tokens already exist, skip browser — still refresh tool cache.
        if storage.has_tokens():
            pending.auth_ready.set()

        auth = self._oauth_provider(
            server,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        try:
            await self._list_tools_for(server, auth)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP OAuth connect failed for %s", server.id)
            pending.error = str(exc)
            pending.auth_ready.set()
            if not pending.code_future.done():
                pending.code_future.set_exception(exc)
            raise
        finally:
            # Drop pending maps once the flow settles.
            self._pending_by_server.pop(server.id, None)
            for state, item in list(self._pending_by_state.items()):
                if item is pending:
                    self._pending_by_state.pop(state, None)

    async def complete_oauth(
        self,
        *,
        code: str,
        state: str,
    ) -> str:
        pending = self._pending_by_state.get(state)
        if not pending:
            raise KeyError("Unknown or expired OAuth state")
        if not pending.code_future.done():
            pending.code_future.set_result((code, state))
        # Wait briefly so tokens are written before the popup closes.
        if pending.task:
            try:
                await asyncio.wait_for(asyncio.shield(pending.task), timeout=60)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        return pending.server_id

    async def disconnect(self, server_id: str) -> None:
        if not get_server(server_id):
            raise KeyError("Server not found")
        await self._cancel_pending(server_id)
        self._storage(server_id).clear()
        self._tool_cache.pop(server_id, None)

    async def _cancel_pending(self, server_id: str) -> None:
        pending = self._pending_by_server.pop(server_id, None)
        if not pending:
            return
        for state, item in list(self._pending_by_state.items()):
            if item is pending:
                self._pending_by_state.pop(state, None)
        if pending.task and not pending.task.done():
            pending.task.cancel()
            try:
                await pending.task
            except Exception:  # noqa: BLE001
                pass

    async def _wait_task(self, pending: _PendingOAuth) -> None:
        if pending.task:
            await pending.task


_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager
