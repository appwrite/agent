"""Load Appwrite agent skills vendored under `.agents/skills/`."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# Repo root: app/graph/skills.py → parents[2]
_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _ROOT / ".agents" / "skills"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


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


def load_skill(name: str, *, max_chars: int = 14000) -> str:
    """Load a skill body (frontmatter stripped)."""
    key = (name or "").strip().lower()
    if key in {"", "list", "all"}:
        return skill_index_text()

    # Accept bare language names: "typescript" → "appwrite-typescript"
    if not key.startswith("appwrite-"):
        key = f"appwrite-{key}"

    # Flutter shares the Dart SDK skill.
    if key in {"appwrite-flutter", "appwrite-flutter-sdk"}:
        key = "appwrite-dart"

    path = skills_dir() / key / "SKILL.md"
    if not path.is_file():
        available = ", ".join(r["name"] for r in list_skill_meta()) or "(none)"
        return f"Unknown skill {name!r}. Available: {available}"

    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    body = text[m.end() :] if m else text
    body = body.strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    return f"# Skill: {key}\n\n{body}"
