"""Build identity baked into the image (see Dockerfile ARG/ENV)."""

from __future__ import annotations

import os

BUILD_ID = os.getenv("AGENT_BUILD_ID", "dev").strip() or "dev"
BUILD_TIME = os.getenv("AGENT_BUILD_TIME", "unknown").strip() or "unknown"


def as_dict() -> dict[str, str]:
    return {
        "build_id": BUILD_ID,
        "build_time": BUILD_TIME,
    }
