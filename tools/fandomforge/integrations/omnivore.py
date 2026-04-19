"""Omnivore adapter.

Omnivore (https://github.com/damato/omnivore locally at ~/Projects/omnivore)
is a URL/file ingestion engine with a rich backend system, SQLite+FTS5 catalog,
CAS storage, and a doctor command. We use it via subprocess for two things:

1. **Lore fetching** — `ff lore fetch "Leon Kennedy RE4"` runs
   `omnivore fetch <wiki-url>` to scrape fandom wiki pages into cleaned
   markdown that the fandom-researcher expert agent can use as grounded
   context.

2. **Video / audio ingestion** — optional. `ff sources add <url>` can
   delegate to Omnivore for YouTube/Vimeo trailer downloads with polite
   rate limiting and robots.txt respect. Currently the FandomForge
   `sources/download.py` handles this directly; this adapter provides the
   alternate path.

The subprocess approach keeps Omnivore's deps (Playwright, Chromium, etc)
out of FandomForge's default install, matching the user-confirmed mixed
integration shape.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["OmnivoreResult", "omnivore_available", "fetch_lore", "search_catalog"]


@dataclass
class OmnivoreResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    payload: Any = None


def _find_omnivore_binary() -> str | None:
    """Find the `omnivore` CLI. Prefers $FANDOMFORGE_OMNIVORE, then PATH,
    then the sibling checkout's venv."""
    override = os.environ.get("FANDOMFORGE_OMNIVORE")
    if override and Path(override).exists():
        return override
    path_bin = shutil.which("omnivore")
    if path_bin:
        return path_bin
    sibling = Path.home() / "Projects" / "omnivore" / ".venv" / "bin" / "omnivore"
    if sibling.exists():
        return str(sibling)
    return None


def omnivore_available() -> bool:
    return _find_omnivore_binary() is not None


def _run(args: list[str], *, timeout_sec: int = 300) -> OmnivoreResult:
    binary = _find_omnivore_binary()
    if binary is None:
        return OmnivoreResult(
            ok=False,
            exit_code=127,
            stdout="",
            stderr="omnivore binary not found. Set FANDOMFORGE_OMNIVORE or install "
                   "Omnivore at ~/Projects/omnivore with its venv active.",
        )
    try:
        res = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        return OmnivoreResult(
            ok=False,
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\ntimed out after {timeout_sec}s",
        )

    payload: Any = None
    if res.stdout.lstrip().startswith("{") or res.stdout.lstrip().startswith("["):
        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError:
            payload = None

    return OmnivoreResult(
        ok=res.returncode == 0,
        exit_code=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
        payload=payload,
    )


def fetch_lore(
    url: str,
    *,
    tags: list[str] | None = None,
    headless: bool = True,
    timeout_sec: int = 180,
) -> OmnivoreResult:
    """Fetch a lore/wiki URL via Omnivore.

    `omnivore fetch <url> --tag fandomforge [--tag <extra>] --headless`
    """
    tags = list(tags or [])
    if "fandomforge" not in tags:
        tags.insert(0, "fandomforge")
    args = ["fetch", url]
    for t in tags:
        args += ["--tag", t]
    if headless:
        args.append("--headless")
    return _run(args, timeout_sec=timeout_sec)


def search_catalog(
    query: str,
    *,
    tag: str | None = "fandomforge",
    limit: int = 20,
) -> OmnivoreResult:
    """Full-text search Omnivore's catalog."""
    args = ["search", query, "--limit", str(limit), "--json"]
    if tag:
        args += ["--tag", tag]
    return _run(args, timeout_sec=60)


def list_recent(limit: int = 20, tag: str | None = "fandomforge") -> OmnivoreResult:
    """List recent ingested items."""
    args = ["catalog", "--limit", str(limit), "--json"]
    if tag:
        args += ["--tag", tag]
    return _run(args, timeout_sec=60)
