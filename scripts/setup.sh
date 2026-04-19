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

# 4. Agent symlinks for Claude Code (optional, non-fatal)
echo ""
echo "→ Agent hookup"
if command -v claude >/dev/null 2>&1; then
  mkdir -p .claude/agents 2>/dev/null || true
  if [ -d ".claude/agents" ]; then
    for f in agents/*.md; do
      [ -f "$f" ] || continue
      name=$(basename "$f")
      # Skip README
      [ "$name" = "README.md" ] && continue
      ln -sf "../../agents/$name" ".claude/agents/$name" 2>/dev/null || true
    done
    echo "    agents symlinked to .claude/agents/"
  else
    echo "    (skipped — can't create .claude/agents)"
  fi
else
  echo "    (Claude Code CLI not found — skipping agent symlinks)"
fi

# 5. Smoke test
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
echo "  1. Activate the Python env:    source .venv/bin/activate"
echo "  2. Start the web dashboard:    pnpm dev"
echo "  3. Create your first project:  ff project new my-first-edit"
echo ""
