"""CapCut project export.

CapCut desktop (2026) imports Final Cut Pro XML via Project > Import Project.
We ship the FCPXML bundle under exports/capcut/ plus a simple README.

We also emit a CapCut-flavored `draft_content.json` scaffold matching their
project shape so power users can drop it directly into the CapCut drafts
folder without going through import. It's a minimal draft — CapCut fills in
defaults on first open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from fandomforge.assembly.fcp_project import FCPExportResult, export_fcp_project


@dataclass
class CapCutExportResult:
    project_dir: Path
    fcpxml_result: FCPExportResult
    draft_dir: Path
    readme_path: Path
    warnings: list[str] = field(default_factory=list)


def _capcut_draft(slug: str, shot_list: dict[str, Any], source_catalog: dict[str, Any],
                   audio_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Emit a minimal CapCut draft_content.json scaffold."""
    fps = int(shot_list["fps"])
    width = int(shot_list["resolution"]["width"])
    height = int(shot_list["resolution"]["height"])
    now_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)

    source_by_id = {s["id"]: s for s in source_catalog["sources"]}
    materials: list[dict[str, Any]] = []
    for src_id, src in source_by_id.items():
        materials.append({
            "id": str(uuid.uuid4()),
            "type": "video",
            "path": str(Path(src["path"]).resolve()),
            "width": int(src["media"]["width"]),
            "height": int(src["media"]["height"]),
            "duration": int(float(src["media"]["duration_sec"]) * 1_000_000),
            "fandomforge_source_id": src_id,
        })

    tracks: list[dict[str, Any]] = [{
        "type": "video",
        "segments": [],
    }]
    cursor = 0
    for shot in shot_list["shots"]:
        src = source_by_id.get(shot["source_id"])
        if not src:
            continue
        dur_us = int((shot["duration_frames"] / fps) * 1_000_000)
        tc_start_us = int(_tc_to_sec(shot["source_timecode"]) * 1_000_000)
        tracks[0]["segments"].append({
            "id": str(uuid.uuid4()),
            "material_id": src_id,
            "source_start": tc_start_us,
            "target_timerange": {"start": cursor, "duration": dur_us},
            "source_timerange": {"start": tc_start_us, "duration": dur_us},
        })
        cursor += dur_us

    # Audio track for the song.
    song_layer = None
    for l in (audio_plan or {}).get("layers", []) or []:
        if l.get("role") == "music" and l.get("file"):
            song_layer = l
            break
    if song_layer:
        song_material_id = str(uuid.uuid4())
        materials.append({
            "id": song_material_id,
            "type": "audio",
            "path": str(Path(song_layer["file"]).resolve()),
        })
        tracks.append({
            "type": "audio",
            "segments": [{
                "id": str(uuid.uuid4()),
                "material_id": song_material_id,
                "target_timerange": {"start": 0, "duration": cursor},
                "source_timerange": {"start": 0, "duration": cursor},
                "volume": 1.0,
            }],
        })

    return {
        "fandomforge_slug": slug,
        "create_time": now_us,
        "update_time": now_us,
        "id": str(uuid.uuid4()),
        "canvas_config": {"width": width, "height": height, "ratio": f"{width}:{height}"},
        "config": {
            "fps": fps,
            "lock_keyframe_v2": True,
            "audio_bitrate": 192000,
            "audio_channel_count": 2,
            "audio_sample_rate": 48000,
        },
        "duration": cursor,
        "materials": materials,
        "tracks": tracks,
    }


def _tc_to_sec(tc: str) -> float:
    h, m, s = tc.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def export_capcut_project(
    *,
    project_dir: Path,
    shot_list: dict[str, Any],
    source_catalog: dict[str, Any],
    edit_plan: dict[str, Any],
    audio_plan: dict[str, Any] | None = None,
    color_plan: dict[str, Any] | None = None,
    title_plan: dict[str, Any] | None = None,
    beat_map: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
) -> CapCutExportResult:
    slug = shot_list["project_slug"]
    out_root = project_dir / "exports" / "capcut"
    out_root.mkdir(parents=True, exist_ok=True)

    fcp_result = export_fcp_project(
        project_dir=project_dir,
        shot_list=shot_list,
        source_catalog=source_catalog,
        edit_plan=edit_plan,
        audio_plan=audio_plan,
        color_plan=color_plan,
        title_plan=title_plan,
        beat_map=beat_map,
        qa_report=qa_report,
        output_root=out_root,
    )

    draft_dir = out_root / f"{slug}.capcut_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft = _capcut_draft(slug, shot_list, source_catalog, audio_plan)
    (draft_dir / "draft_content.json").write_text(
        json.dumps(draft, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps({
            "create_time": draft["create_time"],
            "update_time": draft["update_time"],
            "duration": draft["duration"],
            "draft_name": slug,
            "fps": int(shot_list["fps"]),
            "platform": edit_plan.get("platform_target", "master"),
        }, indent=2),
        encoding="utf-8",
    )

    readme_path = out_root / "README-open-in-capcut.md"
    readme_path.write_text(
        _README.format(slug=slug, fcpxml=str(fcp_result.fcpxml_path.resolve()),
                       draft=str(draft_dir.resolve())),
        encoding="utf-8",
    )
    return CapCutExportResult(
        project_dir=out_root,
        fcpxml_result=fcp_result,
        draft_dir=draft_dir,
        readme_path=readme_path,
        warnings=list(fcp_result.warnings),
    )


_README = """# Open {slug} in CapCut

## Option A: FCPXML import (recommended)

1. CapCut Desktop > Project > Import Project > select `{fcpxml}`.
2. CapCut rebuilds the timeline from the FCPXML. Bins map to CapCut's
   `Media` sidebar folders.

## Option B: drop the CapCut draft folder

1. Copy `{draft}` into your CapCut user drafts directory:
   - macOS: `~/Library/Application Support/CapCut/User Data/Drafts`
   - Windows: `%AppData%\\CapCut\\User Data\\Drafts`
2. Restart CapCut. The project appears in the draft list.
3. Open it. CapCut back-fills default effects/transitions; re-link if prompted.
"""
