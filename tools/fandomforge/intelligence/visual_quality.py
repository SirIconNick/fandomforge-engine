"""Per-shot visual-quality scoring via GPT-4o-mini vision.

One-time pass over the shot library: extract a representative frame from
each shot, send to GPT-4o-mini asking specifically about HUD/watermark/
artifact presence. Stamp a `visual_quality` score into the DB so the
broll picker can filter shots even when the original caption didn't
mention UI elements.

Cost: ~$0.003 per shot @ gpt-4o-mini pricing. 3145 shots → ~$9.50.
Runtime: ~20-30 min for 3000 shots (parallelism limited by API rate).

Usage:
    from fandomforge.intelligence.visual_quality import score_library
    stamped = score_library(
        db_path=project_dir / ".shot-library.db",
        raw_dir=project_dir / "raw",
        api_key=os.environ["OPENAI_API_KEY"],
    )
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def ensure_columns(db_path: Path) -> None:
    """Add visual_quality columns to the shots table if missing."""
    conn = sqlite3.connect(str(db_path))
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
        adds = [
            ("visual_quality", "INTEGER"),      # 0-100 overall cleanliness
            ("character_visible", "INTEGER"),   # 0 or 1 — IS THE TARGET CHARACTER IN FRAME
            ("has_hud_overlay", "INTEGER"),     # 0 or 1
            ("has_watermark", "INTEGER"),       # 0 or 1
            ("has_artifact", "INTEGER"),        # 0 or 1 (encoding/upscale artifacts)
            ("visual_quality_note", "TEXT"),    # one-line vision note
        ]
        for name, typ in adds:
            if name not in existing:
                conn.execute(f"ALTER TABLE shots ADD COLUMN {name} {typ}")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Vision pipeline
# ---------------------------------------------------------------------------

_PROMPT = (
    "This is a frame from a video-game cutscene or gameplay clip, for a "
    "Leon Kennedy tribute (Resident Evil series). Return JSON only with:\n"
    "  visual_quality: integer 0-100 (100 = clean cutscene, 50 = minor overlay, "
    "0 = heavy HUD/watermark)\n"
    "  leon_visible: boolean (is Leon Kennedy — young male with brown/blond "
    "hair, often in a leather jacket or tactical gear — clearly visible in "
    "this frame? False for empty rooms, back-of-head-only, shots dominated by "
    "other characters like Claire/Ada/enemies, or sniper-scope POVs)\n"
    "  has_hud_overlay: boolean (ANY on-screen text or UI: gameplay UI like "
    "button prompts / health bar / ammo counter / reticle / crosshair / "
    "interact icons / sniper scope, AND burned-in dialogue subtitles or "
    "captions — if you see any readable text overlay, set this true)\n"
    "  has_watermark: boolean (burned-in logo/text like 'FILMISNOW', "
    "'GMDB.TV', 'GMDEPTV', YouTube play arrow in corner, channel logos)\n"
    "  has_artifact: boolean (encoding artifacts, heavy upscale chromatic "
    "aberration, tearing, corruption)\n"
    "  note: string, 10 words max describing what's onscreen\n"
    "Return ONLY the JSON object."
)


def _extract_frame(video: Path, t_sec: float, out_jpg: Path) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{t_sec:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=640:-1",
        "-q:v", "5", str(out_jpg),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        return out_jpg.exists() and out_jpg.stat().st_size > 1000
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _vision_query(jpg: Path, api_key: str, retries: int = 5) -> dict | None:
    b64 = base64.b64encode(jpg.read_bytes()).decode()
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        "max_tokens": 150,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read())
            return json.loads(j["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            last_err = exc
            # Exponential backoff for rate limits (429) and server errors (5xx)
            if exc.code == 429 or 500 <= exc.code < 600:
                wait = min(60.0, 2.0 ** attempt)
                time.sleep(wait)
            else:
                # 400-series other than 429 won't recover on retry
                break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2.0 * (attempt + 1))
    logger.warning("vision query failed after %d tries: %s", retries + 1, last_err)
    return None


def _score_one(
    shot_id: int,
    source: str,
    start_sec: float,
    duration_sec: float,
    raw_dir: Path,
    api_key: str,
    tmp_dir: Path,
) -> tuple[int, dict | None]:
    video = raw_dir / f"{source}.mp4"
    if not video.exists():
        return shot_id, None
    t = start_sec + duration_sec * 0.5
    jpg = tmp_dir / f"s_{shot_id}.jpg"
    if not _extract_frame(video, t, jpg):
        return shot_id, None
    result = _vision_query(jpg, api_key)
    jpg.unlink(missing_ok=True)
    return shot_id, result


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def score_library(
    db_path: Path,
    raw_dir: Path,
    api_key: str,
    *,
    force: bool = False,
    limit: int | None = None,
    parallel: int = 8,
    progress_every: int = 25,
) -> int:
    """Stamp visual_quality onto every shot in the library.

    Returns the number of shots scored this pass.

    force: if True, rescore shots that already have visual_quality set.
    limit: max shots to score this run (for budget control).
    parallel: simultaneous API calls (8 is usually safe within rate limit).
    """
    ensure_columns(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        where = "" if force else "WHERE visual_quality IS NULL"
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        rows = conn.execute(
            f"SELECT id, source, start_sec, duration_sec FROM shots "
            f"{where} ORDER BY id ASC {limit_clause}"
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    if total == 0:
        print("Nothing to score.")
        return 0

    print(f"Scoring {total} shots via gpt-4o-mini "
          f"(estimated cost ${total * 0.003:.2f}, ~{total * 0.3 / 60:.0f} min)")

    scored = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Reopen DB inside the executor loop so we don't hold a lock
        def worker(row):
            return _score_one(
                row[0], row[1], row[2], row[3],
                raw_dir, api_key, tmp,
            )

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(worker, r) for r in rows]
            conn = sqlite3.connect(str(db_path))
            try:
                for fut in as_completed(futures):
                    shot_id, result = fut.result()
                    if result is None:
                        continue
                    conn.execute(
                        "UPDATE shots SET "
                        "  visual_quality = ?, "
                        "  character_visible = ?, "
                        "  has_hud_overlay = ?, "
                        "  has_watermark = ?, "
                        "  has_artifact = ?, "
                        "  visual_quality_note = ? "
                        "WHERE id = ?",
                        (
                            int(result.get("visual_quality", 50)),
                            int(bool(result.get("leon_visible"))),
                            int(bool(result.get("has_hud_overlay"))),
                            int(bool(result.get("has_watermark"))),
                            int(bool(result.get("has_artifact"))),
                            str(result.get("note", ""))[:200],
                            shot_id,
                        ),
                    )
                    scored += 1
                    if scored % progress_every == 0:
                        conn.commit()
                        print(f"  scored {scored}/{total}")
                conn.commit()
            finally:
                conn.close()

    print(f"Done — scored {scored}/{total} shots")
    return scored


def drop_low_quality_shots(db_path: Path, *, quality_threshold: int = 60) -> int:
    """Mark low-quality shots so the broll picker skips them.

    Instead of deleting, we just set use_rank to a very high number so the
    picker (which orders by use_rank ASC) never reaches them unless the
    library is exhausted.

    Returns number of shots demoted.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "UPDATE shots SET use_rank = 9999 WHERE "
            "COALESCE(visual_quality, 100) < ? OR "
            "COALESCE(has_hud_overlay, 0) = 1 OR "
            "COALESCE(has_watermark, 0) = 1",
            (int(quality_threshold),),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
