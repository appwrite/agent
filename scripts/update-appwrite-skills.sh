#!/usr/bin/env bash
# Refresh vendored Appwrite agent skills from https://github.com/appwrite/agent-skills
#
# Usage:
#   ./scripts/update-appwrite-skills.sh           # update + pick up new skills
#   ./scripts/update-appwrite-skills.sh update    # update installed skills only
#   ./scripts/update-appwrite-skills.sh sync      # reinstall all from upstream
#   ./scripts/update-appwrite-skills.sh list      # show installed skills
#   ./scripts/update-appwrite-skills.sh check     # show lockfile summary
#
# After updating, rebuild the assistant image so Docker picks up new files:
#   docker compose up --build -d assistant

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SOURCE="${APPWRITE_SKILLS_SOURCE:-appwrite/agent-skills}"
SKILLS_DIR=".agents/skills"
LOCK_FILE="skills-lock.json"

need_npx() {
  if ! command -v npx >/dev/null 2>&1; then
    echo "error: npx is required (install Node.js)" >&2
    exit 1
  fi
}

cmd_list() {
  need_npx
  echo "Installed project skills:"
  npx --yes skills list -p --json 2>/dev/null || npx --yes skills list --json
  echo
  if [[ -d "$SKILLS_DIR" ]]; then
    echo "On disk under ${SKILLS_DIR}/:"
    find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -type d -print | sort | sed 's|.*/|- |'
  fi
}

cmd_check() {
  if [[ ! -f "$LOCK_FILE" ]]; then
    echo "No ${LOCK_FILE} yet. Run: $0 sync"
    exit 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import json
from pathlib import Path
lock = json.loads(Path("skills-lock.json").read_text())
skills = lock.get("skills") or {}
print(f"skills-lock.json: {len(skills)} skill(s)")
for name, meta in sorted(skills.items()):
    src = meta.get("source", "?")
    digest = (meta.get("computedHash") or "")[:12]
    print(f"  {name:24}  {src}  {digest}…")
PY
  else
    echo "Lockfile: ${LOCK_FILE}"
    grep -E '"appwrite-|computedHash|source"' "$LOCK_FILE" | head -80
  fi
  echo
  echo "Upstream: https://github.com/${SOURCE}"
  echo "To refresh: $0"
}

cmd_update() {
  need_npx
  echo "→ Updating project skills to latest (${SOURCE})…"
  npx --yes skills update -p -y
}

cmd_sync() {
  need_npx
  echo "→ Reinstalling all skills from ${SOURCE}…"
  # --skill '*' pulls every skill in the package (including newly published ones)
  # -a '*' / -y keep it non-interactive for CI
  # --copy materializes files under .agents/skills (needed for Docker COPY)
  npx --yes skills add "$SOURCE" --skill '*' -a '*' -y --copy
}

cmd_refresh() {
  cmd_update
  echo
  echo "→ Ensuring any newly published upstream skills are installed…"
  cmd_sync
  echo
  cmd_check
  echo
  echo "Done. Commit changes to ${SKILLS_DIR}/ and ${LOCK_FILE}, then:"
  echo "  docker compose up --build -d assistant"
}

usage() {
  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
}

case "${1:-refresh}" in
  -h | --help | help) usage ;;
  update) cmd_update ;;
  sync | reinstall) cmd_sync ;;
  list | ls) cmd_list ;;
  check | status) cmd_check ;;
  refresh | "") cmd_refresh ;;
  *)
    echo "Unknown command: $1" >&2
    usage >&2
    exit 1
    ;;
esac
