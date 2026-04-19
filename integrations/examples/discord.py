"""Minimal Discord webhook consumer for FandomForge events.

Run this as a tiny HTTP server (or host it anywhere) and register its URL
with `ff webhooks add` or via the web UI. When FandomForge fires events,
this relays them to a Discord channel via an incoming-webhook URL.

Usage:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \\
    FF_WEBHOOK_SECRET=<shared-secret> \\
    python integrations/examples/discord.py

Then register http://your-host:8787/hook with FandomForge.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import urllib.request


PORT = int(os.environ.get("FF_WEBHOOK_PORT", "8787"))
DISCORD_URL = os.environ.get("DISCORD_WEBHOOK_URL")
SHARED_SECRET = os.environ.get("FF_WEBHOOK_SECRET")


EMOJI = {
    "pipeline.started": "🚀",
    "pipeline.completed": "✅",
    "pipeline.failed": "💥",
    "qa.gate.passed": "✅",
    "qa.gate.failed": "⚠️",
    "artifact.applied": "📝",
    "export.ready": "🎬",
    "autopilot.step.failed": "💥",
    "autopilot.completed": "✨",
}


def verify_signature(body: bytes, header: str | None) -> bool:
    if not SHARED_SECRET:
        return True  # No shared secret configured — accept all
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(SHARED_SECRET.encode(), body, hashlib.sha256).hexdigest()
    provided = header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


def post_to_discord(event: str, project: str, payload: dict) -> None:
    if not DISCORD_URL:
        print(f"[no DISCORD_WEBHOOK_URL — would have posted {event} for {project}]")
        return
    icon = EMOJI.get(event, "📣")
    content = f"{icon} **{event}** · `{project}`"
    detail = json.dumps(payload)[:800]
    content += f"\n```\n{detail}\n```"
    req = urllib.request.Request(
        DISCORD_URL,
        data=json.dumps({"content": content}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=10)


class HookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        sig = self.headers.get("X-Fandomforge-Signature")
        if not verify_signature(body, sig):
            self.send_response(403)
            self.end_headers()
            return
        try:
            envelope = json.loads(body)
            event = envelope.get("event", "?")
            project = envelope.get("project_slug", "?")
            payload = envelope.get("payload", {})
            post_to_discord(event, project, payload)
        except Exception as exc:  # noqa: BLE001
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(exc).encode())
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_):
        return  # silent


if __name__ == "__main__":
    print(f"FandomForge → Discord relay listening on :{PORT}")
    print(f"DISCORD_WEBHOOK_URL set: {bool(DISCORD_URL)}")
    print(f"FF_WEBHOOK_SECRET set: {bool(SHARED_SECRET)}")
    HTTPServer(("0.0.0.0", PORT), HookHandler).serve_forever()
