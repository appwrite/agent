from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_session_key(
    x_session_api_key: str | None = Header(default=None, alias="X-Session-API-Key"),
) -> str:
    expected = get_settings().session_api_key
    if not expected:
        # Local smoke only — production must set ASSISTANT_API_KEY.
        return ""
    if not x_session_api_key or x_session_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Session-API-Key",
        )
    return x_session_api_key
