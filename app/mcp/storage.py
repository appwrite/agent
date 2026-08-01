"""Per-server OAuth token + client_info persistence."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class FileTokenStorage:
    """MCP TokenStorage backed by JSON files under a server directory."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._tokens_path = self._dir / "tokens.json"
        self._client_path = self._dir / "client_info.json"

    async def get_tokens(self) -> OAuthToken | None:
        if not self._tokens_path.is_file():
            return None
        try:
            return OAuthToken.model_validate_json(
                self._tokens_path.read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens_path.write_text(
            tokens.model_dump_json(indent=2),
            encoding="utf-8",
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if not self._client_path.is_file():
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(
                self._client_path.read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_path.write_text(
            client_info.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        for path in (self._tokens_path, self._client_path):
            if path.is_file():
                path.unlink()

    def has_tokens(self) -> bool:
        if not self._tokens_path.is_file():
            return False
        try:
            data = json.loads(self._tokens_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
        return bool(data.get("access_token"))
