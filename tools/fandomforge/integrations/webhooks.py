"""Webhook dispatcher.

Reads projects/<slug>/webhooks.json, fires signed POST requests to matching
endpoints, returns structured results.

Signing: HMAC-SHA256 over the raw JSON payload, using the endpoint's `secret`.
Header: `X-Fandomforge-Signature: sha256=<hex>`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EventType = str  # matches the enum in webhooks.schema.json


@dataclass
class DispatchResult:
    endpoint_id: str
    url: str
    status: str  # sent | skipped | failed
    http_status: int | None = None
    error: str | None = None


def _sign(payload: str, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _load_config(project_dir: Path) -> dict[str, Any] | None:
    config_path = project_dir / "webhooks.json"
    if not config_path.exists():
        return None
    try:
        return json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def fire(
    event_type: EventType,
    *,
    project_slug: str,
    payload: dict[str, Any],
    project_root: Path | None = None,
    timeout_sec: float = 10.0,
) -> list[DispatchResult]:
    """Fire a webhook event to any endpoints subscribed to event_type."""
    root = project_root or Path.cwd()
    project_dir = root / "projects" / project_slug
    config = _load_config(project_dir)
    if not config:
        return []

    endpoints = config.get("endpoints") or []
    results: list[DispatchResult] = []

    envelope = {
        "event": event_type,
        "project_slug": project_slug,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    body = json.dumps(envelope, separators=(",", ":"))

    # Separate skipped/filtered endpoints from ones we actually need to hit,
    # so a single slow endpoint doesn't stall the rest via sequential urlopen.
    to_dispatch: list[dict[str, Any]] = []
    for ep in endpoints:
        if not ep.get("enabled", True):
            results.append(DispatchResult(
                endpoint_id=ep.get("id", "?"),
                url=ep.get("url", ""),
                status="skipped",
                error="disabled",
            ))
            continue
        if event_type not in (ep.get("events") or []):
            continue
        to_dispatch.append(ep)

    if not to_dispatch:
        return results

    def _post_one(ep: dict[str, Any]) -> DispatchResult:
        req = urllib.request.Request(
            ep["url"],
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FandomForge-Webhook/1",
                "X-Fandomforge-Event": event_type,
                "X-Fandomforge-Project": project_slug,
            },
        )
        secret = ep.get("secret")
        if secret:
            req.add_header("X-Fandomforge-Signature", _sign(body, secret))
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                return DispatchResult(
                    endpoint_id=ep.get("id", "?"),
                    url=ep["url"],
                    status="sent",
                    http_status=resp.status,
                )
        except urllib.error.HTTPError as exc:
            return DispatchResult(
                endpoint_id=ep.get("id", "?"),
                url=ep["url"],
                status="failed",
                http_status=exc.code,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(
                endpoint_id=ep.get("id", "?"),
                url=ep["url"],
                status="failed",
                error=str(exc),
            )

    # Cap concurrency at 8 — more than plenty for the small number of
    # endpoints a single project ever has, and keeps thread overhead tiny.
    max_workers = min(8, len(to_dispatch))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_post_one, ep): ep for ep in to_dispatch}
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


__all__ = ["DispatchResult", "fire"]
