"""Load Appwrite agent skills vendored under `.agents/skills/`."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# Repo root: app/graph/skills.py → parents[2]
_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _ROOT / ".agents" / "skills"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

# Keep skill bodies small: one full SDK guide can be 30k+ chars, and reloading
# several of them in a single agent turn blows the model context window.
DEFAULT_SKILL_MAX_CHARS = 5_000


def skills_dir() -> Path:
    return _SKILLS_DIR


@lru_cache
def list_skill_meta() -> tuple[dict[str, str], ...]:
    """Return ({name, description}, ...) for installed Appwrite skills."""
    root = skills_dir()
    if not root.is_dir():
        return ()
    items: list[dict[str, str]] = []
    for path in sorted(root.glob("*/SKILL.md")):
        name = path.parent.name
        text = path.read_text(encoding="utf-8")
        desc = ""
        m = _FRONTMATTER.match(text)
        if m:
            for line in m.group(1).splitlines():
                if line.lower().startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
        items.append({"name": name, "description": desc or f"Appwrite skill: {name}"})
    return tuple(items)


def skill_index_text() -> str:
    rows = list_skill_meta()
    if not rows:
        return "No Appwrite skills installed under .agents/skills/."
    lines = ["Installed Appwrite skills:"]
    for row in rows:
        lines.append(f"- {row['name']}: {row['description']}")
    return "\n".join(lines)


def resolve_skill_key(name: str) -> str:
    key = (name or "").strip().lower()
    if key in {"", "list", "all", "skills"}:
        return "list"
    if not key.startswith("appwrite-"):
        key = f"appwrite-{key}"
    # Flutter shares the Dart SDK skill.
    if key in {"appwrite-flutter", "appwrite-flutter-sdk"}:
        key = "appwrite-dart"
    return key


def _truncate_markdown(body: str, max_chars: int) -> str:
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    for marker in ("\n## ", "\n### ", "\n# "):
        idx = cut.rfind(marker)
        if idx > max_chars // 3:
            cut = cut[:idx]
            break
    return (
        cut.rstrip()
        + "\n\n…[skill truncated for context size; do not reload — "
        + "use MCP for live project work, or browser_fetch a docs URL "
        + "for a missing section]"
    )


def load_skill(name: str, *, max_chars: int = DEFAULT_SKILL_MAX_CHARS) -> str:
    """Load a skill body (frontmatter stripped), truncated for model context."""
    key = resolve_skill_key(name)
    if key == "list":
        return skill_index_text()

    path = skills_dir() / key / "SKILL.md"
    if not path.is_file():
        available = ", ".join(r["name"] for r in list_skill_meta()) or "(none)"
        return f"Unknown skill {name!r}. Available: {available}"

    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    body = text[m.end() :] if m else text
    body = _truncate_markdown(body.strip(), max_chars)
    return f"# Skill: {key}\n\n{body}"
