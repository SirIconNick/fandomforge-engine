#!/usr/bin/env python3
"""Convert a FandomForge beat-map.json into an EDL file for DaVinci Resolve marker import.

Resolve accepts CSV and EDL imports for markers. EDL is more widely compatible
with older versions, so we generate that.

Usage:
    python scripts/markers-to-resolve.py projects/myedit/beat-map.json \
        --output projects/myedit/markers.edl \
        --fps 24
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def seconds_to_timecode(seconds: float, fps: float) -> str:
    """Convert seconds to HH:MM:SS:FF timecode."""
    total_frames = int(round(seconds * fps))
    frames = total_frames % int(fps)
    total_seconds = total_frames // int(fps)
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"


def generate_edl(beat_map: dict, fps: float) -> str:
    """Build an EDL with markers at each downbeat and drop."""
    lines: list[str] = []
    lines.append("TITLE: FandomForge Beat Map Markers")
    lines.append("FCM: NON-DROP FRAME")
    lines.append("")

    event = 1
    # Downbeat markers
    for idx, time in enumerate(beat_map.get("downbeats", []), start=1):
        tc = seconds_to_timecode(float(time), fps)
        lines.append(f"{event:03d}  BL       V     C        {tc} {tc} {tc} {tc}")
        lines.append(f"|C:ResolveColorGreen |M:Downbeat {idx}")
        lines.append("")
        event += 1

    # Drop markers (red, named by type)
    for drop in beat_map.get("drops", []):
        tc = seconds_to_timecode(float(drop["time"]), fps)
        label = drop.get("type", "drop").replace("_", " ").title()
        lines.append(f"{event:03d}  BL       V     C        {tc} {tc} {tc} {tc}")
        lines.append(f"|C:ResolveColorRed |M:{label} ({drop.get('intensity', 0):.2f})")
        lines.append("")
        event += 1

    return "\n".join(lines)


def generate_csv(beat_map: dict) -> str:
    """Alternative CSV output for Premiere Pro marker import."""
    lines = ["Marker Name,Description,In,Out,Duration,Marker Type"]
    for idx, time in enumerate(beat_map.get("downbeats", []), start=1):
        lines.append(f"Downbeat {idx},downbeat,{time:.3f},{time:.3f},0,Comment")
    for drop in beat_map.get("drops", []):
        label = drop.get("type", "drop").replace("_", " ").title()
        t = float(drop["time"])
        lines.append(f'{label},"intensity {drop.get("intensity", 0):.2f}",{t:.3f},{t:.3f},0,Chapter')
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("beat_map", type=Path, help="Path to beat-map.json")
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=24.0)
    parser.add_argument("--format", choices=["edl", "csv"], default="edl")
    args = parser.parse_args()

    if not args.beat_map.exists():
        print(f"❌  Beat map not found: {args.beat_map}", file=sys.stderr)
        return 1

    with args.beat_map.open("r") as f:
        beat_map = json.load(f)

    if args.format == "edl":
        content = generate_edl(beat_map, args.fps)
    else:
        content = generate_csv(beat_map)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content)
    print(f"✅  Wrote {args.output} ({args.format.upper()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
