#!/usr/bin/env bash
# Start the FandomForge web dashboard on http://localhost:4321.
# Run from the repo root: scripts/dev.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f "web/.env.local" ]; then
  echo "WARN: web/.env.local not found. Expert chat will be disabled."
  echo "      Copy web/.env.local.example and add ANTHROPIC_API_KEY."
  echo ""
fi

cd web
echo "==> Starting web dashboard on http://localhost:4321"
echo "    Ctrl+C to stop."
pnpm dev
