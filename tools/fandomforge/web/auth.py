"""API key authentication for the public FandomForge web UI.

When ``FF_API_KEY`` is unset the server runs open — appropriate for local
dev. When the env var is set, every ``/api/*`` endpoint requires the key
via ``X-API-Key`` header OR the ``?api_key=`` query string.

This is intentionally minimal — there's exactly one user (the owner).
OAuth, JWTs, or per-user quotas are overkill. The key is a bearer
secret the owner knows; anyone with it has full access.

Paths exempted from auth:
* ``GET /`` — the HTML UI itself (so the login page renders with the
  static CSS/JS; the JS prompts for the key and stores it in
  localStorage, then attaches it to subsequent fetches)
* ``GET /static/*`` — CSS/JS assets
* ``GET /api/health`` — for uptime monitors
* ``GET /api/docs`` (FastAPI's auto OpenAPI UI) — optional; disabled
  when running public via ``FF_DISABLE_DOCS=1``

Use via ``app.middleware("http")(require_api_key)`` in server.py.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


_EXEMPT_PATHS = frozenset({
    "/",
    "/api/health",
})
_EXEMPT_PREFIXES = (
    "/static/",
    "/api/docs",
    "/openapi.json",
    "/favicon.ico",
)


def _expected_key() -> str | None:
    """Return the configured API key or None when auth is off."""
    key = os.environ.get("FF_API_KEY", "").strip()
    return key or None


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    q = request.query_params.get("api_key")
    if q:
        return q.strip()
    return None


async def require_api_key(
    request: Request,
    call_next: Callable[[Request], Awaitable],
):
    """FastAPI middleware — gate /api/* behind FF_API_KEY when set.

    CORS preflight (OPTIONS) requests are always exempt — browsers don't
    attach custom headers to preflights, so requiring the key there would
    fail every cross-origin POST/DELETE before it starts. The actual
    subsequent request still gets auth-checked normally.
    """
    expected = _expected_key()
    if expected is None:
        return await call_next(request)

    # CORS preflight — browser never sends custom headers on these
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path in _EXEMPT_PATHS:
        return await call_next(request)
    for prefix in _EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return await call_next(request)

    provided = _extract_key(request)
    if provided != expected:
        logger.warning("auth failed for %s from %s", path, request.client)
        return JSONResponse(
            status_code=401,
            content={"detail": "API key required. Set X-API-Key header or ?api_key= query."},
        )
    return await call_next(request)


def api_key_configured() -> bool:
    """Convenience for /api/health + CLI — does the server expect auth?"""
    return _expected_key() is not None
