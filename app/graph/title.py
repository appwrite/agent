"""Generate a short conversation topic title from the first exchange."""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.graph.builder import _make_llm
from app.graph.content import content_to_text

_TITLE_SYSTEM = """\
You name chat conversations.

Return ONLY a short topic title for the conversation - not a sentence, not a \
question, not a greeting, and not a copy of the user's message.
Use a regular hyphen (-) if needed; never use an em dash or en dash.

Rules:
- 2 to 6 words
- Title Case when natural (e.g. "Create Storage Bucket")
- No quotes, trailing punctuation, or emoji
- Prefer the user's goal / resource (database, bucket, function, auth, …)
- If the topic is unclear, use a terse generic label like "Appwrite Help"
"""


def _sanitize_title(raw: str) -> str:
    title = raw.strip().strip("\"'`")
    title = title.splitlines()[0].strip() if title else ""
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"^title\s*:\s*", "", title, flags=re.IGNORECASE)
    title = title.strip(" \"'`.,;:!?")
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0].strip() or title[:60]
    return title


async def generate_conversation_title(
    *,
    user_message: str,
    assistant_message: str = "",
) -> str:
    """Ask the chat model for a concise topic title."""
    user = (user_message or "").strip()
    assistant = (assistant_message or "").strip()
    if not user and not assistant:
        return ""

    settings = get_settings()
    llm = _make_llm(settings).bind(max_tokens=24)

    prompt = f"User message:\n{user[:1200]}\n"
    if assistant:
        prompt += f"\nAgent reply (context only):\n{assistant[:800]}\n"
    prompt += "\nConversation title:"

    result = await llm.ainvoke(
        [
            SystemMessage(content=_TITLE_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )
    return _sanitize_title(content_to_text(result.content))
