#!/usr/bin/env bash
# Full smoke test — pytest, vitest, typecheck, build.
# Run from the repo root: scripts/smoke-test.sh
#
# Excludes the two sandbox-hanging pytest files documented in memory.

set -euo pipefail

cd "$(dirname "$0")/.."

fail() { echo "FAIL: $1"; exit 1; }

echo "==> pytest (excluding sandbox-hang files)"
pushd tools > /dev/null
.venv/bin/python -m pytest \
  --ignore=tests/test_leon_smoke.py \
  --ignore=tests/test_matchers.py \
  --tb=short -q || fail "pytest"
popd > /dev/null

echo ""
echo "==> vitest"
pushd web > /dev/null
pnpm test || fail "vitest"
echo ""
echo "==> typecheck"
pnpm typecheck || fail "typecheck"
echo ""
echo "==> build"
BUILD_LOG="${TMPDIR:-/tmp}/ff-build.log"
pnpm build > "$BUILD_LOG" 2>&1 || {
  tail -30 "$BUILD_LOG"
  fail "build"
}
grep -E "Compiled successfully" "$BUILD_LOG" | head -1
popd > /dev/null

echo ""
echo "=================================================================="
echo "Smoke test passed."
echo "=================================================================="
