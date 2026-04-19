#!/usr/bin/env bash
# Create a new FandomForge project from templates.
# Usage: ./scripts/new-project.sh <slug>

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <project-slug> [theme-in-quotes]"
  echo "Example: $0 marvel-sacrifice 'Heroes who knew they wouldn't survive'"
  exit 1
fi

SLUG="$1"
THEME="${2:-}"

# Sanitize slug
if [[ ! "$SLUG" =~ ^[a-z0-9_-]+$ ]]; then
  echo "❌  Slug must be lowercase letters, numbers, dashes, underscores."
  exit 1
fi

# Prefer ff CLI if available (handles placeholders)
if command -v ff >/dev/null 2>&1; then
  if [ -n "$THEME" ]; then
    ff project new "$SLUG" --theme "$THEME"
  else
    ff project new "$SLUG"
  fi
  exit $?
fi

# Fallback: plain copy
PROJ="projects/$SLUG"
if [ -e "$PROJ" ]; then
  echo "❌  Project already exists: $PROJ"
  exit 1
fi

mkdir -p "$PROJ"
cp templates/edit-plan/edit-plan.template.md "$PROJ/edit-plan.md"
cp templates/shot-list/shot-list.template.md "$PROJ/shot-list.md"
cp templates/beat-map/beat-map.template.md "$PROJ/beat-map.md"

if [ -n "$THEME" ]; then
  # macOS / BSD sed compatibility
  sed -i.bak "s|{{ONE_SENTENCE_THEME}}|$THEME|g; s|{{THEME}}|$THEME|g" "$PROJ"/*.md
  rm -f "$PROJ"/*.bak
fi

# Normalize PROJECT_NAME
sed -i.bak "s|{{PROJECT_NAME}}|$SLUG|g" "$PROJ"/*.md 2>/dev/null || true
rm -f "$PROJ"/*.bak 2>/dev/null || true

echo "✅  Created $PROJ"
echo "   Next: cd $PROJ && \$EDITOR edit-plan.md"
