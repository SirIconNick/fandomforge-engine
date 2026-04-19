"""Batch-analyze every reference video in a folder and aggregate profiles.

Output:
  <refs_dir>/../.ref-profiles/<stem>.json      — one profile per video
  <refs_dir>/../.style-template.json           — aggregated template
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from fandomforge.intelligence.reference_analyzer import (
    analyze,
    aggregate_profiles,
    save_profile,
    StyleProfile,
)


def _load_cached(cache_dir: Path, stem: str) -> StyleProfile | None:
    p = cache_dir / f"{stem}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return StyleProfile(**data)
    except Exception:  # noqa: BLE001
        return None


def run(refs_dir: str | Path, *, limit: int | None = None) -> dict:
    refs_dir = Path(refs_dir)
    cache_dir = refs_dir.parent / ".ref-profiles"
    cache_dir.mkdir(parents=True, exist_ok=True)

    videos = sorted(p for p in refs_dir.glob("*.mp4") if p.is_file())
    if limit:
        videos = videos[:limit]

    profiles: list[StyleProfile] = []
    for i, video in enumerate(videos, start=1):
        stem = video.stem
        cached = _load_cached(cache_dir, stem)
        if cached and cached.num_cuts > 0:
            profiles.append(cached)
            print(f"[{i:3d}/{len(videos)}] CACHED {stem}", flush=True)
            continue

        try:
            prof = analyze(video)
            save_profile(prof, cache_dir / f"{stem}.json")
            profiles.append(prof)
            print(
                f"[{i:3d}/{len(videos)}] {stem}  "
                f"{prof.duration_sec:.0f}s  {prof.tempo_bpm:.0f}BPM  "
                f"{prof.num_cuts}cuts  VO={prof.vo_coverage_pct:.0f}%",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{i:3d}/{len(videos)}] FAIL {stem}: {exc}", flush=True)

    agg = aggregate_profiles(profiles)
    agg_path = refs_dir.parent / ".style-template.json"
    agg_path.write_text(json.dumps(agg, indent=2))
    print(f"\nAggregated {len(profiles)} profiles -> {agg_path}")
    print(json.dumps(agg, indent=2))
    return agg


if __name__ == "__main__":
    refs = sys.argv[1] if len(sys.argv) > 1 else "references"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run(refs, limit=limit)
