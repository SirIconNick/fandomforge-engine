"""Enhanced NLE export for FandomForge.

Produces rich timelines from an EditPlan with full marker tracks, color
metadata per shot, SFX placement, and four output formats:
  - FCPXML v1.10 (DaVinci Resolve, Premiere, Final Cut Pro)
  - EDL with extended comments
  - OpenTimelineIO OTIO (JSON interchange)
  - Adobe Premiere XML (legacy format)

Marker tracks produced:
  - Shot-transition markers labeled with intent, mood, and era
  - Beat and downbeat markers from song_structure data
  - Drop and breath moment markers
  - Dialogue cue start and end markers

Color metadata per shot is embedded as marker annotations and XML attributes
so DaVinci Resolve can read the LUT reference and intensity on import.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .shot_optimizer import EditPlan, ShotRecord, VOPlacement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported export formats
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS = frozenset({"fcpxml", "edl", "otio", "premiere_xml"})


# ---------------------------------------------------------------------------
# Marker dataclass
# ---------------------------------------------------------------------------

@dataclass
class TimelineMarker:
    """A single marker placed on the NLE timeline.

    Attributes:
        time_sec: Timeline position in seconds.
        label: Short display label visible in the NLE.
        note: Longer free-text annotation (supported by FCPXML and OTIO).
        color: Marker colour hint for the NLE. One of:
            "red", "orange", "yellow", "green", "blue", "purple", "grey".
        marker_type: Category of marker for programmatic grouping.
        duration_sec: Marker span in seconds. 0.0 for point markers.
    """

    time_sec: float
    label: str
    note: str = ""
    color: str = "blue"
    marker_type: str = "generic"
    duration_sec: float = 0.0


@dataclass
class ExportResult:
    """Return value from export().

    Attributes:
        path: Path to the written output file.
        format: Format name (fcpxml, edl, otio, premiere_xml).
        markers_count: Total number of markers written.
        warnings: Non-fatal issues encountered during export.
    """

    path: Path
    format: str
    markers_count: int
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Marker collection builders
# ---------------------------------------------------------------------------

def _build_shot_markers(shots: list[ShotRecord]) -> list[TimelineMarker]:
    """One marker per shot transition, labelled with intent, mood, and era.

    Args:
        shots: Ordered shot list from the EditPlan.

    Returns:
        List of TimelineMarker instances, one per shot.
    """
    markers: list[TimelineMarker] = []

    mood_color_map = {
        "peak": "red",
        "tense": "orange",
        "calm": "green",
        "breather": "blue",
    }

    for shot in shots:
        color = mood_color_map.get(shot.mood_profile, "blue")
        era_tag = f" [{shot.era}]" if shot.era else ""
        label = f"#{shot.cut_index:03d} {shot.slot_name}{era_tag}"

        # Extract LUT metadata from intent if present
        lut_note = ""
        if "lut_intensity=" in shot.intent:
            import re
            m = re.search(r"lut=([\w_]+)\s+intensity=([\d.]+)", shot.intent)
            if m:
                lut_note = f" | lut={m.group(1)} @{m.group(2)}"

        note = (
            f"mood={shot.mood_profile} | char={shot.character_main or '?'} | "
            f"action={shot.action or '?'} | emotion={shot.emotion or '?'} | "
            f"source={shot.source}{era_tag}{lut_note} | {shot.intent[:120]}"
        )
        markers.append(TimelineMarker(
            time_sec=shot.start_time,
            label=label,
            note=note,
            color=color,
            marker_type="shot",
            duration_sec=shot.duration,
        ))

    return markers


def _build_beat_markers(song_structure_data: dict[str, Any]) -> list[TimelineMarker]:
    """Beat and downbeat markers from song structure JSON.

    Args:
        song_structure_data: Parsed JSON from song_structure analysis.
            Expected keys: beats (list of {time, is_downbeat}), downbeats (list of float).

    Returns:
        List of TimelineMarker, one per beat (downbeats coloured differently).
    """
    markers: list[TimelineMarker] = []
    beats = song_structure_data.get("beats", [])
    downbeat_times = set(song_structure_data.get("downbeats", []))

    for beat in beats:
        t = float(beat.get("time", 0.0))
        is_db = beat.get("is_downbeat", False) or (t in downbeat_times)
        bar_pos = beat.get("bar_position", 1)

        if is_db:
            label = f"DB bar"
            color = "purple"
        else:
            label = f"beat {bar_pos}"
            color = "grey"

        markers.append(TimelineMarker(
            time_sec=round(t, 4),
            label=label,
            note=f"bar_position={bar_pos} is_downbeat={is_db}",
            color=color,
            marker_type="beat",
        ))

    return markers


def _build_drop_markers(song_structure_data: dict[str, Any]) -> list[TimelineMarker]:
    """Drop, buildup, breakdown, and breath markers from song structure.

    Args:
        song_structure_data: Parsed song structure JSON.

    Returns:
        List of TimelineMarker for drops, buildups, and breakdowns.
    """
    markers: list[TimelineMarker] = []

    for drop in song_structure_data.get("drops", []):
        t = float(drop.get("time", 0.0))
        drop_type = drop.get("type", "drop")
        intensity = drop.get("intensity", 0.0)
        markers.append(TimelineMarker(
            time_sec=round(t, 4),
            label=f"DROP {drop_type}",
            note=f"type={drop_type} intensity={intensity:.2f}",
            color="red",
            marker_type="drop",
        ))

    for buildup in song_structure_data.get("buildups", []):
        t = float(buildup.get("start_time", buildup.get("time", 0.0)))
        markers.append(TimelineMarker(
            time_sec=round(t, 4),
            label="BUILD",
            note=f"buildup start",
            color="orange",
            marker_type="buildup",
        ))

    for breakdown in song_structure_data.get("breakdowns", []):
        t = float(breakdown.get("start_time", breakdown.get("time", 0.0)))
        markers.append(TimelineMarker(
            time_sec=round(t, 4),
            label="BREATH",
            note="breakdown/breath moment",
            color="green",
            marker_type="breakdown",
        ))

    return markers


def _build_dialogue_markers(placements: list[VOPlacement]) -> list[TimelineMarker]:
    """Start and end markers for each dialogue cue.

    Args:
        placements: VO cue placements from the EditPlan.

    Returns:
        Two TimelineMarker instances per cue: one at start, one at end.
    """
    markers: list[TimelineMarker] = []
    for i, cue in enumerate(placements):
        short_line = cue.expected_line[:40]
        markers.append(TimelineMarker(
            time_sec=round(cue.start_time, 4),
            label=f"VO{i} IN: {short_line}",
            note=f"dialogue start | cut_index={cue.cut_index} | \"{cue.expected_line}\"",
            color="yellow",
            marker_type="dialogue_in",
        ))
        end_time = round(cue.start_time + cue.duration, 4)
        markers.append(TimelineMarker(
            time_sec=end_time,
            label=f"VO{i} OUT",
            note=f"dialogue end | cut_index={cue.cut_index}",
            color="yellow",
            marker_type="dialogue_out",
            duration_sec=0.0,
        ))
    return markers


def _build_sfx_markers(shots: list[ShotRecord]) -> list[TimelineMarker]:
    """Auto-place SFX cue markers at high-intensity editorial moments.

    Heuristic: place an SFX marker at the start of every peak-mood shot
    and at every shot immediately after a beat-aligned cut.

    Args:
        shots: Ordered shot list.

    Returns:
        List of TimelineMarker for suggested SFX placements.
    """
    markers: list[TimelineMarker] = []
    for shot in shots:
        if shot.mood_profile == "peak":
            markers.append(TimelineMarker(
                time_sec=round(shot.start_time, 4),
                label=f"SFX peak #{shot.cut_index:03d}",
                note=f"peak mood hit | {shot.action or 'action'} | suggest: whoosh/impact",
                color="orange",
                marker_type="sfx",
            ))
        elif shot.is_downbeat:
            markers.append(TimelineMarker(
                time_sec=round(shot.start_time, 4),
                label=f"SFX db #{shot.cut_index:03d}",
                note=f"downbeat cut | suggest: subtle hit/tick",
                color="grey",
                marker_type="sfx",
            ))
    return markers


# ---------------------------------------------------------------------------
# Timecode helpers
# ---------------------------------------------------------------------------

def _secs_to_tc(seconds: float, fps: float = 24.0) -> str:
    """Convert seconds to SMPTE HH:MM:SS:FF timecode.

    Args:
        seconds: Timeline position in seconds.
        fps: Frame rate.

    Returns:
        SMPTE timecode string.
    """
    total_frames = int(round(seconds * fps))
    ff = total_frames % int(fps)
    total_secs = total_frames // int(fps)
    ss = total_secs % 60
    total_mins = total_secs // 60
    mm = total_mins % 60
    hh = total_mins // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _frames_str(seconds: float, fps: int = 24) -> str:
    """Return FCPXML frame fraction string, e.g. '576/24s'.

    Args:
        seconds: Duration or position in seconds.
        fps: Frame rate as integer.

    Returns:
        Frame-fraction string.
    """
    frames = int(round(seconds * fps))
    return f"{frames}/{fps}s"


# ---------------------------------------------------------------------------
# FCPXML v1.10 exporter
# ---------------------------------------------------------------------------

def _export_fcpxml(
    plan: EditPlan,
    all_markers: list[TimelineMarker],
    output_path: Path,
    options: dict[str, Any],
) -> int:
    """Write an FCPXML v1.10 file with all marker tracks.

    Args:
        plan: The EditPlan to export.
        all_markers: Full combined marker list.
        output_path: Destination path.
        options: Dict with optional keys: fps (int), width, height, title,
            audio_track_path.

    Returns:
        Total marker count written.
    """
    fps: int = int(options.get("fps", 24))
    width: int = int(options.get("width", 1920))
    height: int = int(options.get("height", 1080))
    title: str = str(options.get("title", "FandomForge Export"))
    audio_track_path: str | None = options.get("audio_track_path")

    shots = plan.shots
    total_frames = int(round(sum(s.duration for s in shots) * fps)) if shots else int(plan.metadata.total_duration_sec * fps)

    def xs(s: str) -> str:
        return escape(s, {'"': "&quot;", "'": "&apos;"})

    # Collect unique source files
    unique_sources: dict[str, str] = {}
    for s in shots:
        unique_sources.setdefault(s.source, s.source)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE fcpxml>',
        '<fcpxml version="1.10">',
        '  <resources>',
        f'    <format id="r0" name="FFVideoFormat{width}p{fps}" '
        f'frameDuration="1/{fps}s" width="{width}" height="{height}"/>',
    ]

    asset_ids: dict[str, str] = {}
    raw_dir_hint = options.get("raw_dir", "")
    for i, source_name in enumerate(unique_sources, start=1):
        aid = f"r{i}"
        asset_ids[source_name] = aid
        # Build a best-effort file URL from raw_dir if provided. Prefer the
        # exact-name match (e.g. "leon-re4r-cutscenes.mp4") over variants
        # with suffix labels ("leon-re4r-cutscenes.360p.mp4") so the
        # timeline picks the canonical live source, not a backup.
        if raw_dir_hint:
            from urllib.parse import quote
            raw_path = Path(raw_dir_hint)
            video_exts = {".mp4", ".mkv", ".webm", ".mov"}
            exact = [
                raw_path / f"{source_name}{ext}" for ext in video_exts
                if (raw_path / f"{source_name}{ext}").exists()
            ]
            if exact:
                chosen = exact[0]
            else:
                candidates = sorted(raw_path.glob(f"{source_name}.*"))
                video_files = [p for p in candidates if p.suffix.lower() in video_exts]
                chosen = video_files[0] if video_files else None
            if chosen is not None:
                # URL-encode path to handle spaces ("Video Project") correctly
                encoded = quote(chosen.resolve().as_posix(), safe="/")
                file_url = f"file://{encoded}"
            else:
                file_url = f"file:///MISSING/{source_name}"
        else:
            file_url = f"file:///MISSING/{source_name}"
        parts.append(
            f'    <asset id="{aid}" name="{xs(source_name)}" '
            f'src="{xs(file_url)}" hasVideo="1" hasAudio="1" format="r0" '
            f'audioRate="48000" start="0s" duration="{3600 * fps}/{fps}s"/>'
        )

    audio_asset_id = None
    audio_asset_frames = total_frames
    if audio_track_path:
        from urllib.parse import quote
        audio_asset_id = f"r{len(unique_sources) + 1}"
        audio_encoded = quote(Path(audio_track_path).resolve().as_posix(), safe="/")
        audio_url = f"file://{audio_encoded}"
        # Probe actual audio duration so asset-clip dur <= asset dur (else
        # Resolve truncates or shows silence).
        try:
            import subprocess as _sp
            r = _sp.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(audio_track_path)],
                capture_output=True, text=True, timeout=15,
            )
            audio_asset_frames = max(1, int(round(float(r.stdout.strip()) * fps)))
        except Exception:
            pass
        parts.append(
            f'    <asset id="{audio_asset_id}" name="FandomForge_Mix" '
            f'src="{xs(audio_url)}" hasAudio="1" format="r0" '
            f'audioRate="48000" start="0s" duration="{audio_asset_frames}/{fps}s"/>'
        )

    parts += [
        '  </resources>',
        '  <library>',
        f'    <event name="{xs(title)}">',
        f'      <project name="{xs(title)}">',
        f'        <sequence format="r0" tcStart="0s" tcFormat="NDF" '
        f'duration="{total_frames}/{fps}s" audioLayout="stereo" audioRate="48k">',
        '          <spine>',
    ]

    # Video clips — placed back-to-back so no black-frame gaps sneak in.
    # Each clip's offset is exactly the prior clip's end; clips are
    # video-only (audio muted via enabled="0" on audio channel) so the
    # source dialogue audio doesn't compete with the mixed track.
    sorted_shots = sorted(shots, key=lambda s: s.start_time)
    timeline_cursor = 0  # running frame count of spine

    for shot in sorted_shots:
        aid = asset_ids.get(shot.source, "r1")
        dur_f = max(1, int(round(shot.duration * fps)))
        src_f = int(round(shot.clip_start_sec * fps))

        # LUT metadata from intent
        lut_attr = ""
        if "lut=" in shot.intent:
            import re
            m = re.search(r"lut=([\w_]+)\s+intensity=([\d.]+)", shot.intent)
            if m:
                lut_attr = f' lut="{xs(m.group(1))}" lutIntensity="{m.group(2)}"'

        # Video-only: embed an audio-channel element with enabled="0" to
        # silence the source audio. Keeps the mixed_audio track as the
        # only audio the viewer hears.
        parts.append(
            f'            <asset-clip ref="{aid}" '
            f'offset="{timeline_cursor}/{fps}s" duration="{dur_f}/{fps}s" '
            f'start="{src_f}/{fps}s" '
            f'name="{xs(shot.intent[:60])}"'
            f'{lut_attr}>'
        )
        parts.append(
            f'              <audio-channel-source srcCh="1, 2" enabled="0"/>'
        )
        parts.append('            </asset-clip>')
        timeline_cursor += dur_f

    # Audio track
    if audio_asset_id:
        # Match asset-clip duration to the actual audio asset duration so
        # Resolve doesn't try to read beyond EOF (which can manifest as
        # silence at the start or a late-starting song).
        audio_clip_frames = min(audio_asset_frames, total_frames)
        parts.append(
            f'            <asset-clip ref="{audio_asset_id}" '
            f'offset="0s" duration="{audio_clip_frames}/{fps}s" '
            f'start="0s" lane="-1" name="FandomForge Mix" audioRole="music"/>'
        )

    # VO clips on lane -2
    for cue in plan.dialogue_placements:
        vo_path = Path(cue.audio_path)
        if vo_path.exists():
            parts.append(
                f'            <!-- VO: "{xs(cue.expected_line[:60])}" '
                f'@ {cue.start_time:.3f}s dur={cue.duration:.3f}s lane=-2 -->'
            )

    parts.append('          </spine>')

    # Marker track -- FCPXML markers attach to the sequence element
    # We write them as XML comments inside the spine and also as proper
    # <marker> elements on the sequence (both approaches for NLE compatibility)
    marker_count = 0
    for m in sorted(all_markers, key=lambda x: x.time_sec):
        mf = int(round(m.time_sec * fps))
        note_safe = xs(m.note[:200]) if m.note else ""
        label_safe = xs(m.label[:80])
        parts.append(
            f'          <marker start="{mf}/{fps}s" duration="1/{fps}s" '
            f'value="{label_safe}" note="{note_safe}"/>'
        )
        marker_count += 1

    parts += [
        '        </sequence>',
        '      </project>',
        '    </event>',
        '  </library>',
        '</fcpxml>',
    ]

    output_path.write_text("\n".join(parts), encoding="utf-8")
    return marker_count


# ---------------------------------------------------------------------------
# EDL exporter (extended comments)
# ---------------------------------------------------------------------------

def _export_edl(
    plan: EditPlan,
    all_markers: list[TimelineMarker],
    output_path: Path,
    options: dict[str, Any],
) -> int:
    """Write an EDL with extended comment lines for markers.

    Args:
        plan: EditPlan.
        all_markers: Combined markers.
        output_path: Destination.
        options: fps, title keys.

    Returns:
        Marker count.
    """
    fps: float = float(options.get("fps", 24.0))
    title: str = str(options.get("title", "FandomForge Timeline"))

    lines: list[str] = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
    ]

    for i, shot in enumerate(plan.shots, start=1):
        src_in = _secs_to_tc(shot.clip_start_sec, fps)
        src_out = _secs_to_tc(shot.clip_start_sec + shot.duration, fps)
        rec_in = _secs_to_tc(shot.start_time, fps)
        rec_out = _secs_to_tc(shot.start_time + shot.duration, fps)
        reel = f"AX{i:03d}"

        lines.append(f"{i:03d}  {reel}  V     C        {src_in} {src_out} {rec_in} {rec_out}")
        lines.append(f"* FROM CLIP NAME: {shot.source}")
        lines.append(f"* SHOT: {shot.cut_index:03d} | SLOT: {shot.slot_name} | MOOD: {shot.mood_profile}")
        lines.append(f"* ERA: {shot.era or 'unknown'} | CHAR: {shot.character_main or '?'} | ACTION: {shot.action or '?'}")
        lines.append(f"* INTENT: {shot.intent[:120]}")
        if "lut=" in shot.intent:
            import re
            m_lut = re.search(r"lut=([\w_]+)\s+intensity=([\d.]+)", shot.intent)
            if m_lut:
                lines.append(f"* COLOR: lut={m_lut.group(1)} intensity={m_lut.group(2)}")
        lines.append("")

    # Append markers as comment block at end of EDL
    lines.append("* --- MARKERS ---")
    marker_count = 0
    for mk in sorted(all_markers, key=lambda x: x.time_sec):
        tc = _secs_to_tc(mk.time_sec, fps)
        lines.append(f"* MARKER [{mk.marker_type.upper()}] @ {tc} | {mk.label} | {mk.note[:100]}")
        marker_count += 1

    # VO cues
    for i, cue in enumerate(plan.dialogue_placements):
        tc_in = _secs_to_tc(cue.start_time, fps)
        tc_out = _secs_to_tc(cue.start_time + cue.duration, fps)
        lines.append(f"* VO CUE {i}: IN={tc_in} OUT={tc_out} | \"{cue.expected_line[:60]}\"")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return marker_count


# ---------------------------------------------------------------------------
# OpenTimelineIO exporter
# ---------------------------------------------------------------------------

def _export_otio(
    plan: EditPlan,
    all_markers: list[TimelineMarker],
    output_path: Path,
    options: dict[str, Any],
) -> int:
    """Write an OpenTimelineIO JSON (.otio) file.

    OTIO is the modern interchange format supported by Resolve, Premiere,
    Flame, Nuke Studio, and others.

    Args:
        plan: EditPlan.
        all_markers: Combined markers.
        output_path: Destination.
        options: fps, title, raw_dir keys.

    Returns:
        Marker count written.
    """
    fps: float = float(options.get("fps", 24.0))
    title: str = str(options.get("title", "FandomForge Export"))
    raw_dir_hint: str = str(options.get("raw_dir", ""))

    def _rt(seconds: float) -> dict[str, Any]:
        """RationalTime dict."""
        return {"OTIO_SCHEMA": "RationalTime.1", "value": round(seconds * fps, 6), "rate": fps}

    def _tr(start_sec: float, dur_sec: float) -> dict[str, Any]:
        """TimeRange dict."""
        return {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": _rt(start_sec),
            "duration": _rt(dur_sec),
        }

    def _media_ref(source_name: str) -> dict[str, Any]:
        if raw_dir_hint:
            raw_path = Path(raw_dir_hint)
            candidates = list(raw_path.glob(f"{source_name}.*"))
            video_files = [p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
            url = f"file://{video_files[0].resolve().as_posix()}" if video_files else f"file:///MISSING/{source_name}"
        else:
            url = f"file:///MISSING/{source_name}"
        return {
            "OTIO_SCHEMA": "ExternalReference.1",
            "metadata": {},
            "name": source_name,
            "available_range": _tr(0.0, 3600.0),
            "target_url": url,
        }

    # Build clips for video track
    video_clips: list[dict[str, Any]] = []
    for shot in plan.shots:
        lut_meta: dict[str, Any] = {}
        if "lut=" in shot.intent:
            import re
            m_lut = re.search(r"lut=([\w_]+)\s+intensity=([\d.]+)", shot.intent)
            if m_lut:
                lut_meta = {"lut_name": m_lut.group(1), "lut_intensity": float(m_lut.group(2))}

        clip_markers: list[dict[str, Any]] = []
        # Shot-specific markers (attach to clip rather than global timeline)
        for mk in [m for m in all_markers if m.marker_type == "shot" and abs(m.time_sec - shot.start_time) < 0.01]:
            clip_markers.append({
                "OTIO_SCHEMA": "Marker.1",
                "marked_range": _tr(0.0, mk.duration_sec or (1.0 / fps)),
                "color": mk.color.upper(),
                "name": mk.label,
                "comment": mk.note[:200],
                "metadata": {"ff_type": mk.marker_type},
            })

        video_clips.append({
            "OTIO_SCHEMA": "Clip.1",
            "name": f"#{shot.cut_index:03d} {shot.slot_name}",
            "media_reference": _media_ref(shot.source),
            "source_range": _tr(shot.clip_start_sec, shot.duration),
            "markers": clip_markers,
            "metadata": {
                "fandomforge": {
                    "cut_index": shot.cut_index,
                    "slot_name": shot.slot_name,
                    "mood_profile": shot.mood_profile,
                    "era": shot.era,
                    "character_main": shot.character_main,
                    "action": shot.action,
                    "emotion": shot.emotion,
                    "beat_aligned": shot.beat_aligned,
                    "is_downbeat": shot.is_downbeat,
                    "intent": shot.intent[:200],
                    **lut_meta,
                }
            },
        })

    # Build VO clips for dialogue track
    vo_clips: list[dict[str, Any]] = []
    for cue in plan.dialogue_placements:
        vo_clips.append({
            "OTIO_SCHEMA": "Clip.1",
            "name": f"VO: {cue.expected_line[:40]}",
            "media_reference": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "metadata": {},
                "name": Path(cue.audio_path).name,
                "available_range": _tr(0.0, cue.duration),
                "target_url": f"file://{Path(cue.audio_path).resolve().as_posix()}",
            },
            "source_range": _tr(0.0, cue.duration),
            "metadata": {"fandomforge": {"cut_index": cue.cut_index, "line": cue.expected_line}},
        })

    # Global timeline markers (beats, drops, sfx)
    global_markers: list[dict[str, Any]] = []
    for mk in sorted(all_markers, key=lambda x: x.time_sec):
        if mk.marker_type in ("beat", "drop", "buildup", "breakdown", "sfx", "dialogue_in", "dialogue_out"):
            global_markers.append({
                "OTIO_SCHEMA": "Marker.1",
                "marked_range": _tr(mk.time_sec, mk.duration_sec or (1.0 / fps)),
                "color": mk.color.upper(),
                "name": mk.label,
                "comment": mk.note[:200],
                "metadata": {"ff_type": mk.marker_type},
            })

    total_dur = plan.metadata.total_duration_sec

    otio_doc = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": title,
        "global_start_time": _rt(0.0),
        "metadata": {
            "fandomforge": {
                "template": plan.metadata.template_name,
                "total_shots": plan.metadata.total_shots,
                "vo_coverage_pct": plan.metadata.vo_coverage_pct,
                "beat_aligned_pct": plan.metadata.beat_aligned_pct,
            }
        },
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": [
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": "Video",
                    "kind": "Video",
                    "children": video_clips,
                    "markers": global_markers,
                    "metadata": {},
                },
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": "Dialogue",
                    "kind": "Audio",
                    "children": vo_clips,
                    "markers": [],
                    "metadata": {},
                },
            ],
            "markers": [],
            "metadata": {},
        },
    }

    output_path.write_text(json.dumps(otio_doc, indent=2), encoding="utf-8")
    return len(global_markers)


# ---------------------------------------------------------------------------
# Adobe Premiere XML (legacy)
# ---------------------------------------------------------------------------

def _export_premiere_xml(
    plan: EditPlan,
    all_markers: list[TimelineMarker],
    output_path: Path,
    options: dict[str, Any],
) -> int:
    """Write an Adobe Premiere Pro XML (legacy .xml) timeline file.

    This format predates FCPXML and is still supported by many Adobe tools
    for round-trip exchange. Markers are written as <marker> elements with
    a comment and in and out points.

    Args:
        plan: EditPlan.
        all_markers: Combined markers.
        output_path: Destination.
        options: fps, width, height, title, raw_dir keys.

    Returns:
        Marker count.
    """
    fps: float = float(options.get("fps", 24.0))
    width: int = int(options.get("width", 1920))
    height: int = int(options.get("height", 1080))
    title: str = str(options.get("title", "FandomForge Export"))
    raw_dir_hint: str = str(options.get("raw_dir", ""))

    def xs(s: str) -> str:
        return escape(str(s), {'"': "&quot;", "'": "&apos;"})

    def _frames(sec: float) -> int:
        return int(round(sec * fps))

    def _path_for_source(source_name: str) -> str:
        if raw_dir_hint:
            raw_path = Path(raw_dir_hint)
            candidates = list(raw_path.glob(f"{source_name}.*"))
            video_files = [p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
            if video_files:
                return str(video_files[0].resolve())
        return f"/MISSING/{source_name}"

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE xmeml>',
        '<xmeml version="4">',
        f'  <sequence>',
        f'    <name>{xs(title)}</name>',
        f'    <duration>{_frames(plan.metadata.total_duration_sec)}</duration>',
        f'    <rate><timebase>{int(fps)}</timebase><ntsc>FALSE</ntsc></rate>',
        f'    <media>',
        f'      <video>',
        f'        <format>',
        f'          <samplecharacteristics>',
        f'            <width>{width}</width>',
        f'            <height>{height}</height>',
        f'            <pixelaspectratio>square</pixelaspectratio>',
        f'            <rate><timebase>{int(fps)}</timebase><ntsc>FALSE</ntsc></rate>',
        f'          </samplecharacteristics>',
        f'        </format>',
        f'        <track>',
    ]

    for shot in plan.shots:
        clip_path = _path_for_source(shot.source)
        start_f = _frames(shot.start_time)
        end_f = _frames(shot.start_time + shot.duration)
        in_f = _frames(shot.clip_start_sec)
        out_f = _frames(shot.clip_start_sec + shot.duration)

        parts += [
            f'          <clipitem>',
            f'            <name>{xs(shot.source)}</name>',
            f'            <start>{start_f}</start>',
            f'            <end>{end_f}</end>',
            f'            <in>{in_f}</in>',
            f'            <out>{out_f}</out>',
            f'            <file>',
            f'              <name>{xs(shot.source)}</name>',
            f'              <pathurl>file://{xs(clip_path)}</pathurl>',
            f'              <rate><timebase>{int(fps)}</timebase><ntsc>FALSE</ntsc></rate>',
            f'              <media><video/></media>',
            f'            </file>',
            f'            <comments>',
            f'              <mastercomment1>{xs(shot.intent[:120])}</mastercomment1>',
            f'              <mastercomment2>slot={xs(shot.slot_name)} mood={xs(shot.mood_profile)} era={xs(shot.era or "")}</mastercomment2>',
            f'            </comments>',
            f'          </clipitem>',
        ]

    parts.append('        </track>')
    parts.append('      </video>')

    # Audio track for VO
    parts.append('      <audio>')
    parts.append('        <track>')
    for cue in plan.dialogue_placements:
        vo_path = str(Path(cue.audio_path).resolve())
        start_f = _frames(cue.start_time)
        end_f = _frames(cue.start_time + cue.duration)
        parts += [
            f'          <clipitem>',
            f'            <name>{xs(Path(cue.audio_path).name)}</name>',
            f'            <start>{start_f}</start>',
            f'            <end>{end_f}</end>',
            f'            <in>0</in>',
            f'            <out>{_frames(cue.duration)}</out>',
            f'            <file>',
            f'              <name>{xs(Path(cue.audio_path).name)}</name>',
            f'              <pathurl>file://{xs(vo_path)}</pathurl>',
            f'              <rate><timebase>{int(fps)}</timebase><ntsc>FALSE</ntsc></rate>',
            f'              <media><audio/></media>',
            f'            </file>',
            f'          </clipitem>',
        ]

    parts.append('        </track>')
    parts.append('      </audio>')
    parts.append('    </media>')

    # Markers
    marker_count = 0
    for mk in sorted(all_markers, key=lambda x: x.time_sec):
        in_f = _frames(mk.time_sec)
        out_f = _frames(mk.time_sec + mk.duration_sec) if mk.duration_sec > 0 else in_f + 1
        parts += [
            f'    <marker>',
            f'      <comment>{xs(mk.label)}: {xs(mk.note[:120])}</comment>',
            f'      <in>{in_f}</in>',
            f'      <out>{out_f}</out>',
            f'    </marker>',
        ]
        marker_count += 1

    parts += [
        '  </sequence>',
        '</xmeml>',
    ]

    output_path.write_text("\n".join(parts), encoding="utf-8")
    return marker_count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export(
    edit_plan: EditPlan,
    format: str,
    output_path: Path | str,
    options: dict[str, Any] | None = None,
    song_structure_data: dict[str, Any] | None = None,
) -> ExportResult:
    """Export an EditPlan to an NLE timeline file with full marker tracks.

    Args:
        edit_plan: The plan to export.
        format: One of "fcpxml", "edl", "otio", "premiere_xml".
        output_path: Destination file path. Parent directories are created.
        options: Optional dict of format-specific options:
            - fps (int): Frame rate, default 24.
            - width (int): Frame width, default 1920.
            - height (int): Frame height, default 1080.
            - title (str): Project/sequence name.
            - audio_track_path (str): Path to mixed audio file (FCPXML only).
            - raw_dir (str): Root directory for source video files.
        song_structure_data: Parsed song structure JSON dict. If provided,
            beat and drop markers are added. If None, those tracks are omitted.

    Returns:
        ExportResult with path, format, marker count, and any warnings.

    Raises:
        ValueError: If format is not in SUPPORTED_FORMATS.
    """
    fmt = format.lower().strip()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format '{format}'. Choose from: {sorted(SUPPORTED_FORMATS)}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    opts = options or {}
    warnings: list[str] = []

    # Build marker collection
    shot_markers = _build_shot_markers(edit_plan.shots)
    dialogue_markers = _build_dialogue_markers(edit_plan.dialogue_placements)
    sfx_markers = _build_sfx_markers(edit_plan.shots)

    beat_markers: list[TimelineMarker] = []
    drop_markers: list[TimelineMarker] = []
    if song_structure_data:
        beat_markers = _build_beat_markers(song_structure_data)
        drop_markers = _build_drop_markers(song_structure_data)
    else:
        warnings.append("song_structure_data not provided -- beat/drop markers omitted")

    all_markers = shot_markers + beat_markers + drop_markers + dialogue_markers + sfx_markers
    all_markers.sort(key=lambda m: m.time_sec)

    logger.info(
        "Exporting %s: %d shots, %d VO cues, %d markers total",
        fmt, len(edit_plan.shots), len(edit_plan.dialogue_placements), len(all_markers),
    )

    marker_count: int = 0

    try:
        if fmt == "fcpxml":
            marker_count = _export_fcpxml(edit_plan, all_markers, output_path, opts)
        elif fmt == "edl":
            marker_count = _export_edl(edit_plan, all_markers, output_path, opts)
        elif fmt == "otio":
            marker_count = _export_otio(edit_plan, all_markers, output_path, opts)
        elif fmt == "premiere_xml":
            marker_count = _export_premiere_xml(edit_plan, all_markers, output_path, opts)
    except Exception as exc:
        logger.exception("Export failed: %s", exc)
        warnings.append(f"Export error: {exc}")
        return ExportResult(
            path=output_path,
            format=fmt,
            markers_count=0,
            warnings=warnings,
        )

    logger.info("Export complete: %s (%d markers)", output_path.name, marker_count)

    return ExportResult(
        path=output_path,
        format=fmt,
        markers_count=marker_count,
        warnings=warnings,
    )
