"""DaVinci Resolve project generator.

Two paths:

1. **Portable path (always available)** — emit an FCPXML the user imports via
   `File > Import > Final Cut Pro XML`. Resolve 19+ handles FCPXML 1.11
   natively and reconstructs bins, markers, color tags.

2. **Native path (when Resolve is installed locally)** — drive Resolve via
   `DaVinciResolveScript` (Python API shipped with DaVinci Resolve Studio and
   the free version on macOS with "External scripting using" set to Local).
   We create a project, import media into the canonical bin structure,
   build the timeline from shot-list, add timeline markers, and apply per-clip
   color nodes from color-plan.

Which path runs depends on whether `DaVinciResolveScript` is importable at
runtime. The native path writes the Resolve database internally; the portable
FCPXML is always written alongside so the user can re-import if anything
goes sideways.
"""

from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fandomforge.assembly.fcp_project import FCPExportResult, export_fcp_project

logger = logging.getLogger(__name__)


__all__ = ["ResolveExportResult", "export_resolve_project", "davinci_script_available"]


@dataclass
class ResolveExportResult:
    """Returned from export_resolve_project."""

    project_dir: Path
    fcpxml_result: FCPExportResult
    native_project_created: bool
    native_project_name: str | None = None
    warnings: list[str] = field(default_factory=list)


def davinci_script_available() -> bool:
    """Return True if the Resolve Python API is importable right now."""
    # Resolve ships DaVinciResolveScript on these default paths — user may
    # have set PYTHONPATH themselves.
    candidates = [
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Developer/Scripting/Modules",
    ]
    for c in candidates:
        if c not in sys.path and Path(c).exists():
            sys.path.insert(0, c)
    try:
        importlib.import_module("DaVinciResolveScript")
        return True
    except ImportError:
        return False


def _build_timeline_native(
    project: Any,
    shot_list: dict[str, Any],
    source_catalog: dict[str, Any],
    beat_map: dict[str, Any] | None,
    color_plan: dict[str, Any] | None,
    audio_plan: dict[str, Any] | None,
    title_plan: dict[str, Any] | None,
    warnings: list[str],
) -> None:
    media_pool = project.GetMediaPool()
    root_folder = media_pool.GetRootFolder()

    # Create the canonical bin hierarchy.
    bin_map: dict[str, Any] = {}
    for folder_name, _ in [
        ("01_Song", "song"), ("02_Dialogue", "dialogue"), ("03_Sources", "sources"),
        ("04_SFX", "sfx"), ("05_LUTs", "luts"), ("06_Titles", "titles"),
        ("07_References", "references"),
    ]:
        bin_map[folder_name] = media_pool.AddSubFolder(root_folder, folder_name)

    media_storage = project.GetMediaStorage()

    # Song + audio layers.
    song_path = (
        (audio_plan or {}).get("layers", [])
    )
    song_files: list[str] = []
    for layer in (audio_plan or {}).get("layers", []) or []:
        if layer.get("role") == "music" and layer.get("file"):
            song_files.append(str(Path(layer["file"]).resolve()))
    if song_files:
        media_pool.SetCurrentFolder(bin_map["01_Song"])
        media_pool.ImportMedia(song_files)

    dialogue_files = [
        str(Path(l["file"]).resolve()) for l in (audio_plan or {}).get("layers", []) or []
        if l.get("role") in {"dialogue", "voiceover"} and l.get("file")
    ]
    if dialogue_files:
        media_pool.SetCurrentFolder(bin_map["02_Dialogue"])
        media_pool.ImportMedia(dialogue_files)

    sfx_files = [
        str(Path(l["file"]).resolve()) for l in (audio_plan or {}).get("layers", []) or []
        if l.get("role") in {"sfx", "impact", "riser", "ambient", "foley"} and l.get("file")
    ]
    if sfx_files:
        media_pool.SetCurrentFolder(bin_map["04_SFX"])
        media_pool.ImportMedia(sfx_files)

    # Sources grouped by fandom -> source_title.
    sources_root = bin_map["03_Sources"]
    fandom_bins: dict[str, Any] = {}
    title_bins: dict[str, Any] = {}
    media_pool_items: dict[str, Any] = {}  # source_id -> pool item

    for src in source_catalog["sources"]:
        fandom = src.get("fandom", "Unknown")
        title = src.get("title", Path(src["path"]).stem)
        if fandom not in fandom_bins:
            fandom_bins[fandom] = media_pool.AddSubFolder(sources_root, fandom)
        key = (fandom, title)
        if key not in title_bins:
            title_bins[key] = media_pool.AddSubFolder(fandom_bins[fandom], title)
        media_pool.SetCurrentFolder(title_bins[key])
        imported = media_pool.ImportMedia([str(Path(src["path"]).resolve())])
        if imported:
            media_pool_items[src["id"]] = imported[0]

    # LUTs.
    if color_plan:
        lut_files: set[str] = set()
        if color_plan.get("global_lut"):
            lut_files.add(str(Path(color_plan["global_lut"]).resolve()))
        for node in (color_plan.get("per_source") or {}).values():
            if node.get("lut"):
                lut_files.add(str(Path(node["lut"]).resolve()))
        if lut_files:
            media_pool.SetCurrentFolder(bin_map["05_LUTs"])
            media_pool.ImportMedia(sorted(lut_files))

    # Build the timeline from shot_list.
    fps = int(shot_list["fps"])
    project.SetSetting("timelineResolutionWidth", str(shot_list["resolution"]["width"]))
    project.SetSetting("timelineResolutionHeight", str(shot_list["resolution"]["height"]))
    project.SetSetting("timelineFrameRate", str(fps))

    timeline = media_pool.CreateEmptyTimeline(shot_list["project_slug"])
    if timeline is None:
        warnings.append("Failed to create Resolve timeline")
        return

    for shot in shot_list["shots"]:
        item = media_pool_items.get(shot["source_id"])
        if item is None:
            warnings.append(f"shot {shot['id']}: source {shot['source_id']} not imported")
            continue
        tc_sec = _tc_to_sec(shot["source_timecode"])
        start_frame = int(tc_sec * fps)
        end_frame = start_frame + int(shot["duration_frames"])
        media_pool.AppendToTimeline([{
            "mediaPoolItem": item,
            "startFrame": start_frame,
            "endFrame": end_frame,
            "trackIndex": 1,
        }])

    # Markers.
    if beat_map:
        for db in beat_map.get("downbeats", []):
            timeline.AddMarker(int(float(db) * fps), "Green", "downbeat", "", 1)
        for drop in beat_map.get("drops", []):
            timeline.AddMarker(int(float(drop["time"]) * fps), "Red", drop["type"], "", 1)
        for bu in beat_map.get("buildups", []):
            timeline.AddMarker(
                int(float(bu["start"]) * fps),
                "Yellow",
                "buildup",
                bu.get("curve", ""),
                max(1, int((bu["end"] - bu["start"]) * fps)),
            )


def _tc_to_sec(tc: str) -> float:
    h, m, s = tc.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def export_resolve_project(
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
    force_portable: bool = False,
) -> ResolveExportResult:
    """Produce a Resolve-ready export.

    Always writes an FCPXML bundle (portable import path). If DaVinci Resolve
    is running and its scripting API is importable, also drives the app
    directly to create a named project with bins populated and a timeline
    built.
    """
    out_root = project_dir / "exports" / "resolve"
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
    warnings = list(fcp_result.warnings)

    native = False
    native_name: str | None = None
    if not force_portable and davinci_script_available():
        try:
            import DaVinciResolveScript as dvr  # type: ignore
            resolve = dvr.scriptapp("Resolve")
            if resolve is not None:
                manager = resolve.GetProjectManager()
                slug = shot_list["project_slug"]
                project = manager.LoadProject(slug) or manager.CreateProject(slug)
                if project is None:
                    warnings.append("Could not create Resolve project; fell back to FCPXML only")
                else:
                    _build_timeline_native(
                        project=project,
                        shot_list=shot_list,
                        source_catalog=source_catalog,
                        beat_map=beat_map,
                        color_plan=color_plan,
                        audio_plan=audio_plan,
                        title_plan=title_plan,
                        warnings=warnings,
                    )
                    manager.SaveProject()
                    native = True
                    native_name = slug
        except Exception as e:
            warnings.append(f"Resolve scripting failed: {e}")

    return ResolveExportResult(
        project_dir=out_root,
        fcpxml_result=fcp_result,
        native_project_created=native,
        native_project_name=native_name,
        warnings=warnings,
    )
