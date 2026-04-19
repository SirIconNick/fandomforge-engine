"""NLE Export — generate timelines you can open directly in DaVinci Resolve / Premiere.

Formats:
- FCPXML (DaVinci Resolve, Final Cut Pro, Premiere)
- EDL (wide compatibility, basic)
- OTIO (OpenTimelineIO — future-proof interchange)

Why this matters: once in Resolve, you get its built-in AI features for free:
- Show Music Beats (auto beat markers on timeline)
- AI Music Editor (auto-trim music to timeline length)
- Magic Mask (AI rotoscoping)
- Color Match (AI color transfer between clips)
- Neural Engine noise reduction, face refinement, super-scale
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape


@dataclass
class TimelineClip:
    """A single clip on the NLE timeline."""

    source_file: Path
    source_start_sec: float
    duration_sec: float
    timeline_start_sec: float
    name: str = ""


def seconds_to_timecode(seconds: float, fps: float = 24.0) -> str:
    """Convert seconds to SMPTE HH:MM:SS:FF timecode."""
    total_frames = int(round(seconds * fps))
    frames = total_frames % int(fps)
    total_seconds = total_frames // int(fps)
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"


def export_edl(
    clips: list[TimelineClip],
    output_path: Path | str,
    *,
    fps: float = 24.0,
    title: str = "FandomForge Timeline",
) -> bool:
    """Write an EDL (Edit Decision List) file. Simple format, wide compatibility."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
    ]

    for i, clip in enumerate(clips, start=1):
        src_in = seconds_to_timecode(clip.source_start_sec, fps)
        src_out = seconds_to_timecode(clip.source_start_sec + clip.duration_sec, fps)
        rec_in = seconds_to_timecode(clip.timeline_start_sec, fps)
        rec_out = seconds_to_timecode(
            clip.timeline_start_sec + clip.duration_sec, fps
        )
        reel = f"AX{i:03d}"
        lines.append(
            f"{i:03d}  {reel}  V     C        {src_in} {src_out} {rec_in} {rec_out}"
        )
        if clip.name:
            lines.append(f"* FROM CLIP NAME: {clip.name}")
        lines.append(f"* SOURCE: {clip.source_file.name}")
        lines.append("")

    out.write_text("\n".join(lines))
    return True


def export_fcpxml(
    clips: list[TimelineClip],
    output_path: Path | str,
    *,
    fps: int = 24,
    width: int = 1920,
    height: int = 1080,
    title: str = "FandomForge Timeline",
    audio_track_path: Path | str | None = None,
) -> bool:
    """Write an FCPXML v1.10 timeline. Opens directly in Resolve, Premiere, FCP.

    Includes all clips + an optional main audio track (the mixed song + dialogue).
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # FCPXML uses frame-rate-denominator fractions. 24 -> 24000/1001 or 24/1.
    # We use simple whole-frame rates.
    timebase = f"{fps}/1s"
    total_frames = int(round(sum(c.duration_sec for c in clips) * fps))

    def xml_str(s: str) -> str:
        return escape(s, {'"': "&quot;", "'": "&apos;"})

    # Collect unique source files as <asset> entries
    unique_sources: dict[str, Path] = {}
    for c in clips:
        key = c.source_file.name
        unique_sources.setdefault(key, c.source_file)

    audio_path = Path(audio_track_path) if audio_track_path else None

    xml_parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE fcpxml>',
        '<fcpxml version="1.10">',
        '  <resources>',
        f'    <format id="r0" name="FFVideoFormat{width}p{fps}" frameDuration="1/{fps}s" '
        f'width="{width}" height="{height}"/>',
    ]

    asset_ids: dict[str, str] = {}  # source name -> asset id
    for i, (name, path) in enumerate(unique_sources.items(), start=1):
        asset_id = f"r{i}"
        asset_ids[name] = asset_id
        # Use absolute file:// URL so Resolve finds the source
        file_url = f"file://{path.resolve().as_posix()}"
        xml_parts.append(
            f'    <asset id="{asset_id}" name="{xml_str(path.stem)}" '
            f'src="{xml_str(file_url)}" '
            f'hasVideo="1" hasAudio="1" format="r0" audioRate="48000" '
            f'start="0s" duration="{int(1 * 3600 * fps)}/{fps}s"/>'  # overestimate dur
        )

    # If we have a mixed audio track, add it as an asset too
    audio_asset_id = None
    if audio_path and audio_path.exists():
        audio_asset_id = f"r{len(unique_sources) + 1}"
        audio_url = f"file://{audio_path.resolve().as_posix()}"
        xml_parts.append(
            f'    <asset id="{audio_asset_id}" name="{xml_str(audio_path.stem)}" '
            f'src="{xml_str(audio_url)}" '
            f'hasAudio="1" format="r0" audioRate="48000" '
            f'start="0s" duration="{total_frames}/{fps}s"/>'
        )

    xml_parts += [
        '  </resources>',
        '  <library>',
        f'    <event name="{xml_str(title)}">',
        f'      <project name="{xml_str(title)}">',
        f'        <sequence format="r0" tcStart="0s" tcFormat="NDF" '
        f'duration="{total_frames}/{fps}s" audioLayout="stereo" audioRate="48k">',
        '          <spine>',
    ]

    # Video clips on spine
    for c in clips:
        asset_id = asset_ids[c.source_file.name]
        offset_frames = int(round(c.timeline_start_sec * fps))
        duration_frames = int(round(c.duration_sec * fps))
        src_start_frames = int(round(c.source_start_sec * fps))
        xml_parts.append(
            f'            <asset-clip ref="{asset_id}" '
            f'offset="{offset_frames}/{fps}s" '
            f'duration="{duration_frames}/{fps}s" '
            f'start="{src_start_frames}/{fps}s" '
            f'name="{xml_str(c.name or c.source_file.stem)}"/>'
        )

    # Mixed audio track (as a connected clip if present)
    if audio_asset_id:
        xml_parts.append(
            f'            <asset-clip ref="{audio_asset_id}" '
            f'offset="0s" duration="{total_frames}/{fps}s" '
            f'start="0s" lane="-1" name="FandomForge Mix" audioRole="music"/>'
        )

    xml_parts += [
        '          </spine>',
        '        </sequence>',
        '      </project>',
        '    </event>',
        '  </library>',
        '</fcpxml>',
    ]

    out.write_text("\n".join(xml_parts))
    return True


def shots_to_clips(shots: list, raw_dir: Path | str) -> list[TimelineClip]:
    """Convert a parsed shot list into TimelineClip objects.

    Skips placeholder shots (those become gap/black in the NLE). Fills in
    missing source files as warnings (caller can decide).
    """
    raw_dir = Path(raw_dir)
    clips: list[TimelineClip] = []
    timeline_time = 0.0

    for shot in shots:
        if shot.is_placeholder() or not shot.source_id:
            # Placeholder — just advance timeline (gap in NLE)
            timeline_time += shot.duration_sec
            continue

        candidates = list(raw_dir.glob(f"{shot.source_id}.*"))
        video_files = [p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
        if not video_files:
            timeline_time += shot.duration_sec
            continue

        clips.append(
            TimelineClip(
                source_file=video_files[0],
                source_start_sec=shot.source_timestamp_sec or 0.0,
                duration_sec=shot.duration_sec,
                timeline_start_sec=timeline_time,
                name=f"#{shot.number} {shot.hero or shot.description[:30]}",
            )
        )
        timeline_time += shot.duration_sec

    return clips
