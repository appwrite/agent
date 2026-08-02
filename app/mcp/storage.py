"""Request-scoped MCP OAuth token storage (no disk)."""

from __future__ import annotations

from typing import Any

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class MemoryTokenStorage:
    """In-memory TokenStorage, optionally seeded from API/proxy credentials."""

    def __init__(
        self,
        *,
        tokens: dict[str, Any] | OAuthToken | None = None,
        client_info: dict[str, Any] | OAuthClientInformationFull | None = None,
    ) -> None:
        self._tokens = self._parse_tokens(tokens)
        self._client_info = self._parse_client_info(client_info)

    @staticmethod
    def _parse_tokens(
        value: dict[str, Any] | OAuthToken | None,
    ) -> OAuthToken | None:
        if value is None:
            return None
        if isinstance(value, OAuthToken):
            return value
        try:
            return OAuthToken.model_validate(value)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _parse_client_info(
        value: dict[str, Any] | OAuthClientInformationFull | None,
    ) -> OAuthClientInformationFull | None:
        if value is None:
            return None
        if isinstance(value, OAuthClientInformationFull):
            return value
        try:
            return OAuthClientInformationFull.model_validate(value)
        except Exception:  # noqa: BLE001
            return None

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info = client_info

    def has_tokens(self) -> bool:
        return bool(self._tokens and self._tokens.access_token)

    def access_token(self) -> str | None:
        if not self._tokens or not self._tokens.access_token:
            return None
        return self._tokens.access_token

    def export(self) -> dict[str, Any]:
        return {
            "tokens": self._tokens.model_dump(mode="json") if self._tokens else None,
            "client_info": (
                self._client_info.model_dump(mode="json") if self._client_info else None
            ),
        }
