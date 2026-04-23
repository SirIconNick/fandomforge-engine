"""Cloudflare Tunnel wrapper — exposes ``ff serve`` at a public URL.

Two providers supported today:

* **Cloudflare Quick Tunnel** (default) — zero signup, one command. Gives
  a regenerating ``https://<adjective>-<adjective>-<number>.trycloudflare.com``
  URL that changes every time ``cloudflared`` restarts. Good for casual
  personal use where URL stability doesn't matter.

* **Tailscale Funnel** (``--via tailscale``) — free for personal use,
  stable URL (``https://<machine>.<tailnet>.ts.net``). Requires a
  Tailscale account + ``tailscale`` binary installed.

Both paths write the resulting public URL to
``<repo>/.cache/ff/tunnel-url.txt`` so the web UI's /api/health endpoint
can surface it and the ``ff tunnel-url`` command can print it.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_TRYCLOUDFLARE_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")
_TAILSCALE_URL_RE = re.compile(r"https://[-a-z0-9]+\.[a-z0-9]+\.ts\.net")


def _tunnel_url_path(repo_root: Path) -> Path:
    return repo_root / ".cache" / "ff" / "tunnel-url.txt"


def _write_tunnel_url(repo_root: Path, url: str) -> None:
    p = _tunnel_url_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(url + "\n", encoding="utf-8")


def _clear_tunnel_url(repo_root: Path) -> None:
    p = _tunnel_url_path(repo_root)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _which(name: str) -> str | None:
    return shutil.which(name)


def _install_instructions(provider: str) -> str:
    if provider == "cloudflare":
        return (
            "Install cloudflared:\n"
            "  macOS:   brew install cloudflared\n"
            "  Linux:   see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n"
            "Then rerun `ff tunnel`."
        )
    if provider == "tailscale":
        return (
            "Install Tailscale + sign in:\n"
            "  macOS:   brew install tailscale && tailscale up\n"
            "  Linux:   see https://tailscale.com/download\n"
            "Then rerun `ff tunnel --via tailscale`."
        )
    return ""


def run_cloudflare_quick_tunnel(
    repo_root: Path,
    port: int,
    *,
    on_url: Callable[[str], None] | None = None,
    stop_event: "object | None" = None,
) -> int:
    """Spawn `cloudflared tunnel --url http://localhost:<port>` and pipe
    its stdout/stderr, scraping the public trycloudflare URL from the
    log output.

    Blocks until cloudflared exits or ``stop_event`` fires. Returns the
    subprocess exit code.
    """
    if _which("cloudflared") is None:
        print(_install_instructions("cloudflare"), file=sys.stderr)
        return 127

    cmd = [
        "cloudflared", "tunnel",
        "--url", f"http://localhost:{port}",
        "--no-autoupdate",
    ]
    print(f"running: {' '.join(shlex.quote(c) for c in cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    seen_url: str | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            print(line)
            if seen_url is None:
                m = _TRYCLOUDFLARE_RE.search(line)
                if m:
                    seen_url = m.group(0)
                    _write_tunnel_url(repo_root, seen_url)
                    print("\n" + "=" * 60)
                    print(f"  PUBLIC URL:  {seen_url}")
                    print("=" * 60 + "\n")
                    if on_url:
                        try:
                            on_url(seen_url)
                        except Exception:  # noqa: BLE001
                            logger.exception("on_url callback failed")
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                proc.terminate()
                break
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        _clear_tunnel_url(repo_root)
    return proc.returncode or 0


def run_tailscale_funnel(
    repo_root: Path,
    port: int,
) -> int:
    """Expose ``http://localhost:<port>`` via Tailscale Funnel.

    Blocks until ``tailscale funnel`` exits. The URL is read back via
    ``tailscale serve status`` after startup so we can cache it for
    /api/health.
    """
    if _which("tailscale") is None:
        print(_install_instructions("tailscale"), file=sys.stderr)
        return 127

    cmd = ["tailscale", "funnel", "--bg", f"localhost:{port}"]
    print(f"running: {' '.join(shlex.quote(c) for c in cmd)}")
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        return rc

    # Fetch the funnel URL from `tailscale serve status`
    time.sleep(1)
    try:
        out = subprocess.check_output(
            ["tailscale", "serve", "status"],
            text=True,
            timeout=10,
        )
        m = _TAILSCALE_URL_RE.search(out)
        if m:
            _write_tunnel_url(repo_root, m.group(0))
            print("\n" + "=" * 60)
            print(f"  PUBLIC URL:  {m.group(0)}")
            print("=" * 60 + "\n")
            print("Funnel running in background. Stop with:  tailscale funnel reset")
        else:
            print("tailscale serve status didn't surface a .ts.net URL — check `tailscale funnel status`.",
                  file=sys.stderr)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"tailscale serve status failed: {exc}", file=sys.stderr)
    return 0


def stop_tailscale_funnel() -> int:
    """Reset all tailscale serves. Matches the background-mode start."""
    if _which("tailscale") is None:
        print(_install_instructions("tailscale"), file=sys.stderr)
        return 127
    return subprocess.run(["tailscale", "funnel", "reset"], check=False).returncode


def current_tunnel_url(repo_root: Path) -> str:
    p = _tunnel_url_path(repo_root)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
