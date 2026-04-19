"""Canonical bin/folder structure used by every NLE project generator.

Using the same hierarchy across Resolve, Premiere, FCP, CapCut and Vegas
means the editor's mental map travels with them.

    01_Song/
    02_Dialogue/
    03_Sources/
        <fandom>/
            <source_title>/
    04_SFX/
    05_LUTs/
    06_Titles/
    07_References/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BinEntry:
    """A single media item going into a bin."""

    path: Path
    role: str  # "music" | "dialogue" | "source" | "sfx" | "lut" | "title" | "reference"
    name: str
    fandom: str = ""
    source_title: str = ""
    # Optional: per-clip color node list for Resolve power-bins.
    color_nodes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BinLayout:
    """The full bin tree for one project."""

    song: list[BinEntry] = field(default_factory=list)
    dialogue: list[BinEntry] = field(default_factory=list)
    sources: list[BinEntry] = field(default_factory=list)
    sfx: list[BinEntry] = field(default_factory=list)
    luts: list[BinEntry] = field(default_factory=list)
    titles: list[BinEntry] = field(default_factory=list)
    references: list[BinEntry] = field(default_factory=list)

    def all_entries(self) -> list[BinEntry]:
        return (
            self.song + self.dialogue + self.sources +
            self.sfx + self.luts + self.titles + self.references
        )


BIN_ORDER = [
    ("01_Song", "song"),
    ("02_Dialogue", "dialogue"),
    ("03_Sources", "sources"),
    ("04_SFX", "sfx"),
    ("05_LUTs", "luts"),
    ("06_Titles", "titles"),
    ("07_References", "references"),
]


def build_bin_layout(
    *,
    project_dir: Path,
    source_catalog: dict[str, Any],
    audio_plan: dict[str, Any] | None = None,
    color_plan: dict[str, Any] | None = None,
    title_plan: dict[str, Any] | None = None,
    edit_plan: dict[str, Any] | None = None,
) -> BinLayout:
    """Translate the project's artifacts into a canonical BinLayout."""
    layout = BinLayout()

    # Song: whichever file edit-plan / audio-plan points at.
    song_path: Path | None = None
    if audio_plan:
        for layer in audio_plan.get("layers", []):
            if layer.get("role") == "music" and layer.get("file"):
                song_path = Path(layer["file"])
                break
    if song_path is None and edit_plan:
        song_file = (edit_plan.get("song") or {}).get("file")
        if song_file:
            song_path = Path(song_file)
    if song_path is not None:
        layout.song.append(BinEntry(
            path=song_path,
            role="music",
            name=song_path.name,
        ))

    # Dialogue layers from the audio plan.
    if audio_plan:
        for layer in audio_plan.get("layers", []):
            if layer.get("role") in {"dialogue", "voiceover"} and layer.get("file"):
                p = Path(layer["file"])
                layout.dialogue.append(BinEntry(
                    path=p,
                    role="dialogue",
                    name=p.name,
                ))
            elif layer.get("role") in {"sfx", "impact", "riser", "foley"} and layer.get("file"):
                p = Path(layer["file"])
                layout.sfx.append(BinEntry(
                    path=p,
                    role="sfx",
                    name=p.name,
                ))

    # Sources grouped by fandom/source_title.
    for src in source_catalog["sources"]:
        p = Path(src["path"])
        layout.sources.append(BinEntry(
            path=p,
            role="source",
            name=p.name,
            fandom=src.get("fandom", "Unknown"),
            source_title=src.get("title", p.stem),
            color_nodes=(color_plan or {}).get("per_source", {}).get(src["id"], {}).get("nodes", []),
        ))

    # LUTs.
    if color_plan:
        if color_plan.get("global_lut"):
            p = Path(color_plan["global_lut"])
            layout.luts.append(BinEntry(
                path=p,
                role="lut",
                name=p.name,
            ))
        for source_id, node in (color_plan.get("per_source") or {}).items():
            if node.get("lut"):
                p = Path(node["lut"])
                layout.luts.append(BinEntry(
                    path=p,
                    role="lut",
                    name=p.name,
                ))

    # Titles.
    if title_plan:
        for t in title_plan.get("titles", []):
            layout.titles.append(BinEntry(
                path=project_dir / "titles" / f"{t['id']}.fusion",
                role="title",
                name=t.get("text", t["id"])[:40],
            ))

    # Hero color reference image.
    if color_plan and color_plan.get("hero_frame", {}).get("image_path"):
        p = Path(color_plan["hero_frame"]["image_path"])
        layout.references.append(BinEntry(
            path=p,
            role="reference",
            name=p.name,
        ))

    return layout
