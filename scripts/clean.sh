#!/usr/bin/env bash
# Clean build artifacts and caches. Does not touch projects/ or fixtures.
# Run from the repo root: scripts/clean.sh

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> cleaning web/.next and test caches"
rm -rf web/.next web/.turbo web/playwright-report web/test-results
echo "==> cleaning pytest + mypy + ruff caches"
rm -rf tools/.pytest_cache tools/.mypy_cache tools/.ruff_cache
find tools -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
echo "==> cleaning build logs"
rm -f "${TMPDIR:-/tmp}/ff-build.log" "${TMPDIR:-/tmp}/ff-pytest-full.log" 2>/dev/null || true

echo ""
echo "Clean complete. Projects, fixtures, and .env.local preserved."
