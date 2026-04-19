"""Vision-caption scene boundaries so shots can be picked semantically.

For each detected scene, grab a representative frame, send to GPT-4o-mini,
store a one-line description + tags in a JSON cache. Reusable library.
"""

from __future__ import annotations

import base64
import json
import subprocess
import urllib.request
from pathlib import Path


def _encode_frame(video: Path, at_sec: float, max_width: int = 512) -> str | None:
    """Extract one frame at `at_sec` and return base64 JPEG."""
    tmp = Path("/tmp/claude") / f"_frame_{int(at_sec * 1000)}_{video.stem}.jpg"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{at_sec:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={max_width}:-2",
         "-q:v", "6", str(tmp)],
        timeout=30,
    )
    if r.returncode != 0 or not tmp.exists():
        return None
    return base64.b64encode(tmp.read_bytes()).decode()


def _caption_frame(
    api_key: str,
    b64: str,
    hint: str = "",
    vision_context: str = "game cutscene frame",
    character_list: str = "leon, grace, victor, enemy, other",
) -> dict:
    """Send one frame to GPT-4o-mini, get description + tags.

    Args:
        vision_context: Series/setting context (e.g. "Resident Evil 9 game
            cutscene" or "Marvel movie scene"). Injected into the prompt so
            the model knows what franchise this is.
        character_list: Comma-separated character names the model should
            recognize. Tailored per project via project-config.yaml.
    """
    prompt = (
        f"Describe this {vision_context}. Return JSON only: "
        '{"desc": "<1 short sentence>", "tags": ["tag1","tag2",...]}. '
        f"Tags should describe: character present ({character_list}), "
        'action (talking, aiming, shooting, walking, holding_gun, wounded, dead, unconscious, '
        'listening, pointing, reading, driving, running, none), '
        'setting (indoor, outdoor, dark, bright, ruins, hospital, lab, snow, forest, interior, chamber), '
        'and mood (tense, calm, brutal, emotional, warm, quiet, chaotic, still). '
        'Be accurate; omit tags that do not apply. If no person, say character=none.'
    )
    if hint:
        prompt += f" Context hint: {hint}"

    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "low",
                }},
            ]},
        ],
        "max_tokens": 150,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            j = json.loads(r.read())
        txt = j["choices"][0]["message"]["content"].strip()
        return json.loads(txt)
    except Exception as e:  # noqa: BLE001
        return {"desc": f"(error: {e})", "tags": []}


def build_library(
    raw_dir: Path,
    scene_cache: Path,
    output_path: Path,
    api_key: str,
    *,
    time_filters: dict[str, list[tuple[float, float]]] | None = None,
    min_scene_dur: float = 1.5,
    max_scene_dur: float = 8.0,
    limit: int | None = None,
    skip_cached: bool = True,
    vision_context: str = "game cutscene frame",
    character_list: str = "leon, grace, victor, enemy, other",
) -> dict:
    """Caption every scene in scene_cache that passes the filters.

    Args:
        vision_context: Series/setting context injected into the vision prompt
            (e.g. "Resident Evil 9 game cutscene" or "Marvel movie scene").
        character_list: Comma-separated character names the model should
            recognize. Tailored per project via project-config.yaml.
        time_filters: {source_name: [(t_min, t_max), ...]} — only caption
            scenes whose start time falls inside any of those windows.
            If None, caption all.

    Returns the library dict and writes it to output_path.
    """
    scenes = json.loads(scene_cache.read_text())
    existing = {}
    if skip_cached and output_path.exists():
        existing = json.loads(output_path.read_text())

    out = dict(existing)
    total_added = 0
    for source_name, scene_list in scenes.items():
        source_video = raw_dir / f"{source_name}.mp4"
        if not source_video.exists():
            continue

        windows = time_filters.get(source_name) if time_filters else None
        for start, end, dur in scene_list:
            if dur < min_scene_dur or dur > max_scene_dur:
                continue
            if windows and not any(lo <= start <= hi for lo, hi in windows):
                continue
            key = f"{source_name}@{start:.2f}"
            if key in out:
                continue
            # Representative frame at scene_middle
            mid = start + dur / 2
            b64 = _encode_frame(source_video, mid)
            if not b64:
                continue
            cap = _caption_frame(
                api_key, b64,
                vision_context=vision_context,
                character_list=character_list,
            )
            out[key] = {
                "source": source_name,
                "start_sec": start,
                "end_sec": end,
                "duration_sec": dur,
                "desc": cap.get("desc", ""),
                "tags": cap.get("tags", []),
            }
            total_added += 1
            if total_added % 10 == 0:
                output_path.write_text(json.dumps(out, indent=2))
                print(f"  ...captioned {total_added} new scenes, flushed to disk")
            if limit and total_added >= limit:
                output_path.write_text(json.dumps(out, indent=2))
                return out

    output_path.write_text(json.dumps(out, indent=2))
    print(f"  done: +{total_added} new scenes (total {len(out)})")
    return out


def search(
    library: dict,
    *,
    require_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    sources: list[str] | None = None,
    time_ranges: dict[str, list[tuple[float, float]]] | None = None,
    min_dur: float | None = None,
    max_dur: float | None = None,
    text_contains: list[str] | None = None,
) -> list[dict]:
    """Search the captioned library by tags/source/time/keyword."""
    hits = []
    for key, s in library.items():
        tags = set(t.lower() for t in s.get("tags", []))
        desc = s.get("desc", "").lower()

        if require_tags and not all(t.lower() in tags for t in require_tags):
            continue
        if exclude_tags and any(t.lower() in tags for t in exclude_tags):
            continue
        if sources and s["source"] not in sources:
            continue
        if time_ranges and s["source"] in time_ranges:
            windows = time_ranges[s["source"]]
            if not any(lo <= s["start_sec"] <= hi for lo, hi in windows):
                continue
        if min_dur and s["duration_sec"] < min_dur:
            continue
        if max_dur and s["duration_sec"] > max_dur:
            continue
        if text_contains and not all(w.lower() in desc for w in text_contains):
            continue
        hits.append({"key": key, **s})
    return hits
