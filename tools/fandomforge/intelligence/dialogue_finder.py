"""Find exact dialogue timestamps in source videos.

Two strategies:
1. SRT files (fast): parse existing captions from yt-dlp downloads
2. Whisper (slow but universal): transcribe audio, then search
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DialogueMatch:
    source_id: str
    start_sec: float
    end_sec: float
    text: str
    score: float
    via: str  # "srt" or "whisper"

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


@dataclass
class SRTEntry:
    start_sec: float
    end_sec: float
    text: str


def _parse_srt_time(s: str) -> float:
    # Handle both "HH:MM:SS,mmm" and "HH:MM:SS.mmm"
    s = s.replace(",", ".").strip()
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_srt(srt_path: str | Path) -> list[SRTEntry]:
    """Parse an SRT subtitle file into structured entries."""
    path = Path(srt_path)
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8", errors="replace")
    # Split into blocks separated by blank lines
    blocks = re.split(r"\n\s*\n", text.strip())
    entries: list[SRTEntry] = []

    for block in blocks:
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue

        # Skip the index line if present (first line is digits)
        if re.match(r"^\d+$", lines[0]):
            lines = lines[1:]

        if not lines:
            continue

        # Timestamp line: "HH:MM:SS,mmm --> HH:MM:SS,mmm"
        time_match = re.match(
            r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})",
            lines[0],
        )
        if not time_match:
            continue

        try:
            start = _parse_srt_time(time_match.group(1))
            end = _parse_srt_time(time_match.group(2))
        except (ValueError, AttributeError):
            continue

        # Remaining lines are the caption text. Strip HTML/positioning tags.
        raw_text = " ".join(lines[1:])
        clean = re.sub(r"<[^>]*>", "", raw_text)  # strip HTML tags
        clean = re.sub(r"\{[^}]*\}", "", clean)    # strip SSA/SubStation tags
        clean = re.sub(r"\s+", " ", clean).strip()

        if clean:
            entries.append(SRTEntry(start_sec=start, end_sec=end, text=clean))

    return entries


def _fuzzy_score(query: str, text: str) -> float:
    """Simple fuzzy score: fraction of query words appearing in text, in order.

    0.0 = nothing matches, 1.0 = all query words present (in any order).
    Bonus for consecutive matches (phrase preservation).
    """
    q_words = re.findall(r"\w+", query.lower())
    t_words = re.findall(r"\w+", text.lower())
    if not q_words:
        return 0.0
    if not t_words:
        return 0.0

    t_set = set(t_words)
    matched = sum(1 for w in q_words if w in t_set)
    base = matched / len(q_words)

    # Bonus: phrase substring match
    q_phrase = " ".join(q_words)
    t_phrase = " ".join(t_words)
    if q_phrase in t_phrase:
        base = min(1.0, base + 0.3)

    return base


def find_dialogue_in_srt(
    srt_path: str | Path,
    query: str,
    source_id: str = "",
    top_k: int = 5,
    min_score: float = 0.4,
) -> list[DialogueMatch]:
    """Search an SRT file for the query text. Returns ranked matches."""
    entries = parse_srt(srt_path)
    if not entries:
        return []

    scored: list[tuple[float, SRTEntry]] = []
    # Score a sliding window of consecutive entries so we catch lines that
    # were broken across multiple caption blocks.
    for i, entry in enumerate(entries):
        merged_text = entry.text
        merged_end = entry.end_sec
        # Merge with next 1-2 entries to catch lines split across captions
        for j in range(1, 3):
            if i + j < len(entries):
                merged_text += " " + entries[i + j].text
                merged_end = entries[i + j].end_sec
        score = _fuzzy_score(query, merged_text)
        if score >= min_score:
            scored.append(
                (
                    score,
                    SRTEntry(
                        start_sec=entry.start_sec,
                        end_sec=merged_end,
                        text=merged_text,
                    ),
                )
            )

    scored.sort(key=lambda x: -x[0])

    return [
        DialogueMatch(
            source_id=source_id,
            start_sec=e.start_sec,
            end_sec=e.end_sec,
            text=e.text,
            score=score,
            via="srt",
        )
        for score, e in scored[:top_k]
    ]


def find_all_srts(project_dir: str | Path) -> list[Path]:
    """Locate all .srt files in transcripts/ and raw/."""
    base = Path(project_dir)
    srts: list[Path] = []
    for subdir in ["transcripts", "raw"]:
        d = base / subdir
        if d.exists():
            srts.extend(d.glob("*.srt"))
    return sorted(set(srts))


def find_dialogue_across_project(
    project_dir: str | Path,
    query: str,
    top_k: int = 10,
    min_score: float = 0.4,
) -> list[DialogueMatch]:
    """Search ALL SRT files in a project. Returns global ranked matches."""
    all_matches: list[DialogueMatch] = []
    for srt in find_all_srts(project_dir):
        # Source ID is the filename stem, stripping .en.srt -> source_id
        source_id = srt.stem.replace(".en", "").replace(".auto", "")
        matches = find_dialogue_in_srt(
            srt, query, source_id=source_id, top_k=top_k, min_score=min_score
        )
        all_matches.extend(matches)
    all_matches.sort(key=lambda m: -m.score)
    return all_matches[:top_k]


# ---------- Whisper fallback ----------


def _check_whisper_installed() -> bool:
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_with_whisper(
    audio_or_video_path: str | Path,
    output_srt: str | Path,
    *,
    model_size: str = "base",
    language: str = "en",
) -> bool:
    """Run OpenAI Whisper on an audio/video file, output SRT.

    Model sizes: tiny (39MB) / base (74MB) / small (244MB) / medium (769MB) / large (1550MB)
    'base' is a good quality/speed balance; 'medium' for final transcripts.
    """
    if not _check_whisper_installed():
        return False

    if shutil.which("ffmpeg") is None:
        return False

    src = Path(audio_or_video_path)
    out = Path(output_srt)
    if not src.exists():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)

    import whisper

    model = whisper.load_model(model_size)
    result = model.transcribe(str(src), language=language, verbose=False)

    # Write SRT
    with out.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(result.get("segments", []), start=1):
            start = seg["start"]
            end = seg["end"]
            text = seg["text"].strip()
            f.write(f"{i}\n")
            f.write(f"{_sec_to_srt(start)} --> {_sec_to_srt(end)}\n")
            f.write(f"{text}\n\n")

    return True


def _sec_to_srt(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")
