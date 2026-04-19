#!/usr/bin/env bash
# Full smoke: fast smoke (pytest + vitest + typecheck + build) plus the Chrome
# extension end-to-end test.
#
# Use this as the canonical "everything green" check before a release.
#
# Env:
#   FF_EXT_STRICT=1  — if the extension test would skip (no chromium, etc.),
#                      fail instead of skipping.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=================================================================="
echo "[1/2] Fast smoke (pytest, vitest, typecheck, build)"
echo "=================================================================="
scripts/smoke-test.sh

echo ""
echo "=================================================================="
echo "[2/2] Extension end-to-end (dashboard + headless chromium + grab)"
echo "=================================================================="
scripts/test-extension.sh

echo ""
echo "=================================================================="
echo "Full smoke passed."
echo "=================================================================="
