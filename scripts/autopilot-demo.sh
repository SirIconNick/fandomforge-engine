#!/usr/bin/env bash
# Scaffold a demo project and run autopilot end-to-end.
# Run from the repo root: scripts/autopilot-demo.sh [project-slug]
#
# Uses a cached fixture song (run scripts/setup.sh first to populate fixtures).

set -euo pipefail

cd "$(dirname "$0")/.."

SLUG="${1:-demo-autopilot}"
FIXTURE_SONG="tools/tests/fixtures/media/incompetech-sneaky-snitch.mp3"

if [ ! -f "$FIXTURE_SONG" ]; then
  echo "Fixture song not cached. Run: tools/.venv/bin/ff fixtures fetch"
  exit 1
fi

PROJECT_DIR="projects/$SLUG"
if [ -d "$PROJECT_DIR" ]; then
  echo "WARN: $PROJECT_DIR already exists. Autopilot will resume where it left off."
else
  echo "==> Creating project $SLUG"
  tools/.venv/bin/ff project new "$SLUG"
fi

mkdir -p "$PROJECT_DIR/assets"
cp "$FIXTURE_SONG" "$PROJECT_DIR/assets/song.mp3"
echo "  copied song to $PROJECT_DIR/assets/song.mp3"

echo ""
echo "==> Cost estimate"
tools/.venv/bin/ff autopilot --project "$SLUG" --estimate

echo ""
echo "==> Running autopilot"
tools/.venv/bin/ff autopilot --project "$SLUG" --prompt "mentor-loss across Marvel, LOTR, Star Wars"

echo ""
echo "=================================================================="
echo "Autopilot complete for '$SLUG'."
echo ""
echo "Artifacts:"
ls -1 "$PROJECT_DIR/data/" 2>/dev/null | sed 's/^/  /'
echo ""
echo "Journal: $PROJECT_DIR/.history/autopilot.jsonl"
echo ""
echo "Next:"
echo "  - Open http://localhost:4321/projects/$SLUG in the web dashboard"
echo "  - Review the drafted edit-plan and shot-list, edit if needed"
echo "  - Drop real source videos into $PROJECT_DIR/raw/ to enable render stages"
echo "=================================================================="
