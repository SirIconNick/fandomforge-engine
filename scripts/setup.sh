#!/usr/bin/env bash
# FandomForge — one-shot setup script
# Installs Python tools, web dependencies, and verifies ffmpeg.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  FandomForge setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Check required binaries
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌  Missing: $1  — $2"
    return 1
  else
    echo "✅  $1 found: $(command -v "$1")"
  fi
}

echo ""
echo "→ Checking prerequisites"
need python3 "Install Python 3.13+" || exit 1
need node "Install Node.js 24+" || exit 1
need pnpm "npm i -g pnpm" || exit 1
need ffmpeg "brew install ffmpeg" || exit 1
need ffprobe "bundled with ffmpeg" || exit 1

# 2. Python venv + tools
echo ""
echo "→ Python venv + tools"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "    created .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e tools/
echo "    fandomforge tools installed"

# 3. Web dependencies
echo ""
echo "→ Web dependencies"
if [ -d "web" ] && [ -f "web/package.json" ]; then
  (cd web && pnpm install --silent)
  echo "    web dependencies installed"
fi

# 4. Agent hookup — copies (symlinks break when project moves)
echo ""
echo "→ Agent hookup"
mkdir -p .claude/agents 2>/dev/null || true
if [ -d ".claude/agents" ]; then
  for f in agents/*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    [ "$name" = "README.md" ] && continue
    cp "$f" ".claude/agents/$name" 2>/dev/null || true
  done
  count=$(ls .claude/agents/ 2>/dev/null | wc -l | tr -d ' ')
  echo "    $count agents copied to .claude/agents/"
else
  echo "    (skipped — can't create .claude/agents)"
fi

# 5. .env.local scaffolding
echo ""
echo "→ Environment file"
if [ ! -f "web/.env.local" ] && [ -f "web/.env.local.example" ]; then
  cp web/.env.local.example web/.env.local
  echo "    web/.env.local created — edit to add ANTHROPIC_API_KEY"
else
  echo "    web/.env.local already present"
fi

# 6. Fetch legal test fixtures
echo ""
echo "→ Fetching legal test fixtures"
if ff fixtures fetch 2>&1 | grep -E "^\s*(✓|✗|fixtures:)" | tail -8; then
  :
else
  echo "    (some fixtures may not be reachable — that's non-fatal)"
fi

# 7. Smoke test
echo ""
echo "→ Smoke test"
if ff --version >/dev/null 2>&1; then
  echo "    ff --version: $(ff --version)"
else
  echo "❌  ff command not working. Try: source .venv/bin/activate"
  exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Setup complete."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo "  1. Activate the Python env:       source .venv/bin/activate"
echo "  2. Edit web/.env.local:           add ANTHROPIC_API_KEY"
echo "  3. Verify the key:                scripts/verify-anthropic.sh"
echo "  4. Start the web dashboard:       scripts/dev.sh"
echo "  5. Try the auto-pilot demo:       scripts/autopilot-demo.sh"
echo "  6. Full smoke test anytime:       scripts/smoke-test.sh"
echo ""
