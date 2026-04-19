#!/usr/bin/env bash
# End-to-end extension test: boots the dashboard, runs the Playwright driver,
# tears down cleanly.
#
# Prereqs that will cause a skip (exit 2) rather than a fail:
#   - chromium binary not installed (run `pnpm --dir web exec playwright install chromium`)
#   - the `grab-smoketest` project doesn't exist (created automatically)
#
# Env overrides:
#   FF_EXT_STRICT=1   — make every skip a real failure
#   FF_EXT_URL=...    — override the test URL (default: Me at the zoo)
#   FF_EXT_PROJECT=x  — override the project slug (default: grab-smoketest)

set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${FF_EXT_PORT:-4321}"
DASHBOARD="http://localhost:${PORT}"
BUILD_LOG="${TMPDIR:-/tmp}/ff-ext-build.log"
SERVER_LOG="${TMPDIR:-/tmp}/ff-ext-server.log"
SERVER_PID=""
TEST_PROJECT="${FF_EXT_PROJECT:-grab-smoketest}"

cleanup() {
  local exit_code=$?
  if [ -n "${SERVER_PID}" ] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo ""
    echo "==> stopping dashboard (pid ${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    # Give it a second to close gracefully
    sleep 1
    kill -9 "${SERVER_PID}" 2>/dev/null || true
  fi
  exit $exit_code
}
trap cleanup EXIT INT TERM

# ---------- 0. verify ff binary exists ----------
if [ ! -x tools/.venv/bin/ff ]; then
  echo "FAIL: tools/.venv/bin/ff not found. Run scripts/setup.sh first."
  exit 1
fi

# ---------- 1. ensure the test project exists ----------
if [ ! -d "projects/${TEST_PROJECT}" ]; then
  echo "==> creating test project ${TEST_PROJECT}"
  tools/.venv/bin/ff project new "${TEST_PROJECT}" >/dev/null
fi

# ---------- 2. build the web dashboard if needed ----------
if [ ! -d web/.next ] || [ -n "$(find web/src -newer web/.next 2>/dev/null | head -1)" ]; then
  echo "==> building dashboard"
  pnpm --dir web build > "${BUILD_LOG}" 2>&1 || {
    tail -40 "${BUILD_LOG}"
    echo "FAIL: pnpm build failed"
    exit 1
  }
fi

# ---------- 3. free the port if something is already there ----------
if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "==> port ${PORT} already in use — reusing existing dashboard"
  REUSED=1
else
  REUSED=0
  echo "==> starting dashboard on :${PORT}"
  (cd web && PORT="${PORT}" pnpm start > "${SERVER_LOG}" 2>&1) &
  SERVER_PID=$!

  # Wait up to 45s for it to come up
  for i in $(seq 1 45); do
    if curl -sf "${DASHBOARD}/api/projects" >/dev/null 2>&1; then
      echo "==> dashboard up after ${i}s"
      break
    fi
    sleep 1
  done
  if ! curl -sf "${DASHBOARD}/api/projects" >/dev/null 2>&1; then
    echo "FAIL: dashboard did not respond within 45s"
    tail -30 "${SERVER_LOG}"
    exit 1
  fi
fi

# ---------- 4. run the Playwright driver ----------
echo ""
echo "==> running extension test"
set +e
FF_DASHBOARD="${DASHBOARD}" FF_EXT_PROJECT="${TEST_PROJECT}" \
  node browser-extensions/chrome/test-extension.mjs
TEST_RC=$?
set -e

# ---------- 5. classify exit ----------
case "${TEST_RC}" in
  0)
    echo ""
    echo "=================================================================="
    echo "Extension test passed."
    echo "=================================================================="
    ;;
  2)
    echo ""
    echo "=================================================================="
    echo "Extension test SKIPPED (prereqs missing — see log above)."
    echo "Set FF_EXT_STRICT=1 to treat skips as failures."
    echo "=================================================================="
    ;;
  *)
    echo ""
    echo "FAIL: extension test failed (exit ${TEST_RC})"
    exit 1
    ;;
esac

# If we started the server, cleanup will stop it on exit.
# If we reused an existing one, leave it alone.
if [ "${REUSED}" = "1" ]; then
  SERVER_PID=""
fi

exit 0
