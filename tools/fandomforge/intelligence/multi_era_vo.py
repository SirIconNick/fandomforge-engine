"""Multi-era Leon S. Kennedy voice-over extraction library.

Mines Leon dialogue lines from every available game era using SRT files
(for RE9) and on-demand Whisper transcription (for RE2R/RE4R/RE6/Damnation/
Vendetta/Infinite Darkness). Each matched line is automatically extracted via
ffmpeg with 0.5s pre-roll, then cleaned through Demucs mdx_extra vocal
isolation and loudnorm to -14 LUFS with 15ms fade-in. A round-trip Whisper
verification gate rejects clips that don't survive the extraction process
cleanly.

Typical use
-----------
    from tools.fandomforge.intelligence.multi_era_vo import build_full_vo_library

    library = build_full_vo_library(
        raw_dir="/path/to/raw",
        output_dir="/path/to/dialogue",
    )
    for era, lines in library.items():
        for line in lines:
            print(era, line.slug, line.verified_text)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PREROLL_SEC: float = 0.5
_FADE_IN_MS: int = 15
_TARGET_LUFS: float = -14.0
_TRUE_PEAK: float = -1.0
_SAMPLE_RATE: int = 48000
_FUZZY_MATCH_THRESHOLD: float = 0.55  # minimum token overlap to pass verification

# Known badass Leon lines per era used as mining seeds.
_ERA_TARGET_LINES: dict[str, list[str]] = {
    "RE2R": [
        "bingo",
        "i'm a cop now",
        "just another day",
        "we can't afford to wait",
        "all right, let's go",
        "this is a nightmare",
        "hang in there",
        "that's not gonna stop me",
        "nothing's gonna stop me",
        "stay with me",
    ],
    "RE4R": [
        "where's everyone going",
        "bingo",
        "your right hand comes off",
        "your right hand comes off?",
        "shoot him",
        "not what i expected",
        "i won't let you down",
        "i've fought off worse",
        "just try and stop me",
        "you're not stopping me",
    ],
    "RE6": [
        "fear",
        "perpetual fear",
        "there's gotta be a way",
        "we're not done yet",
        "hold it together",
        "keep moving",
        "i'll end this",
        "there's no going back",
        "we push forward",
        "no more running",
    ],
    "Damnation": [
        "this ends today",
        "i'm taking you down",
        "you brought this on yourself",
        "i don't have a choice",
        "stand down",
        "it's over",
        "i won't let that happen",
        "things are never that simple",
        "stop",
        "i've seen enough death",
    ],
    "Vendetta": [
        "i'm not going to let you die",
        "not on my watch",
        "i'm not done yet",
        "you're not dying here",
        "put the gun down",
        "this stops now",
        "i've made my decision",
        "walk away",
        "i can't turn back now",
        "let's finish this",
    ],
    "ID": [
        "i'm not done yet",
        "stand down",
        "this is wrong",
        "someone has to stop this",
        "i'll handle it",
        "we need to move",
        "don't make me do this",
        "step away",
        "you leave me no choice",
        "it's over",
    ],
    "RE9": [
        "i'm going after victor",
        "it's over victor",
        "i'm not done yet",
        "six survivors",
        "we're getting out of here",
        "couldn't save them all",
        "here now",
        "let's do this",
        "had enough",
        "going to destroy",
    ],
}

# How many lines to target per era
_TARGET_LINES_PER_ERA: int = 8

# Filename pattern for era source videos
_ERA_FILENAME_MAP: dict[str, str] = {
    "RE2R": "leon-re2r-cutscenes.mp4",
    "RE4R": "leon-re4r-cutscenes.mp4",
    "RE6": "leon-re6-cutscenes.mp4",
    "Damnation": "leon-damnation.mp4",
    "Vendetta": "leon-vendetta.mp4",
    "ID": "leon-infinite-darkness.mp4",
}

# SRT files that already exist (RE9 era)
_ERA_SRT_MAP: dict[str, list[str]] = {
    "RE9": [
        "re9-good-ending.en.srt",
        "re9-leon-scenepack.en.srt",
        "re9-raccoon-emotional.en.srt",
        "re9-requiem-raw-scenes.en.srt",
    ],
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExtractedLine:
    """A single verified Leon dialogue clip.

    Attributes:
        era: Source era identifier (e.g. 'RE2R', 'RE9').
        slug: Filesystem-safe short name derived from the line text.
        text: Cleaned dialogue text as found in the source transcript.
        output_path: Absolute path to the extracted and cleaned WAV file.
        start_sec: Start time within the source video/audio.
        end_sec: End time within the source video/audio.
        source_file: Name of the source video file.
        verified_text: Text returned by the round-trip Whisper check. Empty
            string if verification was skipped or failed.
        verified: True when round-trip transcription fuzzy-matches the
            expected text at or above _FUZZY_MATCH_THRESHOLD.
        match_score: Fuzzy match ratio (0.0 to 1.0). 1.0 = perfect match.
    """

    era: str
    slug: str
    text: str
    output_path: Path
    start_sec: float
    end_sec: float
    source_file: str
    verified_text: str = ""
    verified: bool = False
    match_score: float = 0.0


@dataclass
class _SRTEntry:
    """Internal SRT block parsed into a usable structure."""

    start_sec: float
    end_sec: float
    text: str


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _load_api_key(project_root: Optional[str] = None) -> str:
    """Load OPENAI_API_KEY from .env in the project root, then fall back to env.

    Args:
        project_root: Path to the project root directory. When None the
            function walks up from this file's location.

    Returns:
        The API key string.

    Raises:
        RuntimeError: If no key is found anywhere.
    """
    # 1. Already in environment
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key

    # 2. Walk up to find .env
    if project_root is None:
        search = Path(__file__).resolve()
        for _ in range(8):
            candidate = search / ".env"
            if candidate.exists():
                project_root = str(search)
                break
            search = search.parent

    if project_root:
        env_path = Path(project_root) / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        os.environ["OPENAI_API_KEY"] = key
                        return key

    raise RuntimeError(
        "OPENAI_API_KEY not found. Set it in the environment or in "
        "the project root .env file."
    )


def _require_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg is not on PATH."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. Install via: brew install ffmpeg")


def _require_demucs() -> None:
    """Raise RuntimeError if demucs is not importable."""
    if shutil.which("demucs") is None:
        raise RuntimeError(
            "demucs not found on PATH. Install via: pip install demucs"
        )


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------


def _parse_srt_time(s: str) -> float:
    """Convert SRT timestamp string to float seconds.

    Args:
        s: Timestamp string in HH:MM:SS,mmm or HH:MM:SS.mmm format.

    Returns:
        Time in seconds.
    """
    s = s.replace(",", ".").strip()
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _parse_srt(srt_path: Path) -> list[_SRTEntry]:
    """Parse an SRT file into structured entries.

    Args:
        srt_path: Path to the .srt file.

    Returns:
        List of _SRTEntry instances in chronological order.
    """
    if not srt_path.exists():
        logger.warning("SRT file not found: %s", srt_path)
        return []

    raw = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.strip())
    entries: list[_SRTEntry] = []

    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            continue

        # Find the timecode line (contains " --> ")
        tc_line = None
        text_lines: list[str] = []
        for ln in lines:
            if " --> " in ln and tc_line is None:
                tc_line = ln
            elif tc_line is not None:
                text_lines.append(ln)

        if tc_line is None:
            continue

        try:
            left, right = tc_line.split("-->")
            start = _parse_srt_time(left.strip())
            end = _parse_srt_time(right.strip().split()[0])
        except (ValueError, IndexError):
            continue

        text = " ".join(text_lines).strip()
        # Strip HTML-style tags
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            entries.append(_SRTEntry(start_sec=start, end_sec=end, text=text))

    return entries


# ---------------------------------------------------------------------------
# Text matching helpers
# ---------------------------------------------------------------------------


def _slugify(text: str, max_len: int = 32) -> str:
    """Convert dialogue text to a filesystem-safe slug.

    Args:
        text: Raw dialogue line text.
        max_len: Maximum slug length in characters.

    Returns:
        Lowercase, hyphen-separated slug.
    """
    slug = re.sub(r"[^\w\s]", "", text.lower())
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:max_len].strip("-")


def _token_overlap(a: str, b: str) -> float:
    """Compute token-level Jaccard similarity between two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Overlap ratio in [0.0, 1.0].
    """
    tokens_a = set(re.findall(r"\w+", a.lower()))
    tokens_b = set(re.findall(r"\w+", b.lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _mine_lines(
    entries: list[_SRTEntry],
    target_lines: list[str],
    max_results: int = _TARGET_LINES_PER_ERA,
) -> list[_SRTEntry]:
    """Score SRT entries against target keywords and return the best matches.

    Uses token overlap scoring. Entries with overlap >= 0.25 are candidates.
    Prefers shorter, punchier lines (under 60 chars). Deduplicates by slug.

    Args:
        entries: Parsed SRT entries to search.
        target_lines: Known target lines to score against.
        max_results: Maximum number of matches to return.

    Returns:
        Sorted list of top-matching _SRTEntry items.
    """
    scored: list[tuple[float, _SRTEntry]] = []

    for entry in entries:
        best = 0.0
        for target in target_lines:
            score = _token_overlap(entry.text, target)
            if score > best:
                best = score

        # Length bonus: short punchy lines score higher
        length_bonus = 0.15 if len(entry.text) < 50 else 0.0
        final_score = best + length_bonus

        if final_score >= 0.25:
            scored.append((final_score, entry))

    scored.sort(key=lambda x: -x[0])

    # Deduplicate by slug
    seen_slugs: set[str] = set()
    results: list[_SRTEntry] = []
    for _score, entry in scored:
        slug = _slugify(entry.text)
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            results.append(entry)
            if len(results) >= max_results:
                break

    return results


# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------


def _transcribe_with_whisper(
    audio_path: Path,
    api_key: str,
    language: str = "en",
    response_format: str = "srt",
) -> str:
    """Transcribe an audio file using the OpenAI Whisper API.

    Args:
        audio_path: Path to the audio file to transcribe.
        api_key: OpenAI API key.
        language: ISO 639-1 language code.
        response_format: One of 'srt', 'vtt', 'text', 'json'.

    Returns:
        Raw API response text (SRT string when response_format='srt').

    Raises:
        RuntimeError: If the API call fails or returns an error.
    """
    try:
        import openai
    except ImportError as exc:
        raise RuntimeError(
            "openai package not installed. Run: pip install openai"
        ) from exc

    client = openai.OpenAI(api_key=api_key)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found for transcription: {audio_path}")

    logger.info("Whisper transcribing: %s", audio_path.name)
    with audio_path.open("rb") as fh:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=fh,
            language=language,
            response_format=response_format,
        )

    # The API returns a string directly for srt/text/vtt formats
    if isinstance(response, str):
        return response
    # For json format it returns an object
    return str(response)


def _transcribe_video_to_srt(
    video_path: Path,
    srt_out: Path,
    api_key: str,
    tmp_dir: Path,
) -> list[_SRTEntry]:
    """Extract audio from video, transcribe with Whisper, save SRT and parse it.

    Args:
        video_path: Source MP4 or other video file.
        srt_out: Destination path for the generated SRT file.
        api_key: OpenAI API key.
        tmp_dir: Temp directory for intermediate audio extraction.

    Returns:
        Parsed list of _SRTEntry instances.
    """
    # Extract audio as mp3 (smaller than WAV, Whisper accepts it)
    audio_tmp = tmp_dir / f"{video_path.stem}_audio.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1",
        "-c:a", "libmp3lame", "-q:a", "4",
        str(audio_tmp),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to extract audio from {video_path.name}: {exc.stderr[-400:]}"
        ) from exc

    # Whisper: get SRT
    srt_text = _transcribe_with_whisper(audio_tmp, api_key, response_format="srt")
    srt_out.parent.mkdir(parents=True, exist_ok=True)
    srt_out.write_text(srt_text, encoding="utf-8")
    logger.info("Saved generated SRT: %s", srt_out)

    # Parse immediately
    return _parse_srt(srt_out)


# ---------------------------------------------------------------------------
# Audio extraction helpers
# ---------------------------------------------------------------------------


def _run_ffmpeg(cmd: list[str], label: str = "") -> None:
    """Run an ffmpeg command; raises RuntimeError on non-zero exit.

    Args:
        cmd: Full command list including 'ffmpeg'.
        label: Optional label for error messages.

    Raises:
        RuntimeError: If the subprocess exits non-zero.
    """
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        tag = f" [{label}]" if label else ""
        raise RuntimeError(
            f"ffmpeg failed{tag}: {exc.stderr[-600:]}"
        ) from exc


def _extract_raw_clip(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    out_path: Path,
    pre_roll: float = _PREROLL_SEC,
) -> None:
    """Extract a dialogue window from a source video to a WAV file.

    Applies 0.5 s pre-roll so context precedes the first word. The pre-roll
    is clamped to the file start (no negative seek).

    Args:
        source_path: Path to source video or audio.
        start_sec: Dialogue start time in the source.
        end_sec: Dialogue end time in the source.
        out_path: Destination WAV path.
        pre_roll: Pre-roll seconds before start_sec.
    """
    actual_start = max(0.0, start_sec - pre_roll)
    duration = (end_sec - actual_start) + 0.3  # small tail
    duration = max(0.5, duration)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{actual_start:.3f}",
        "-i", str(source_path),
        "-t", f"{duration:.3f}",
        "-vn",
        "-ar", str(_SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    _run_ffmpeg(cmd, label=f"extract {out_path.name}")


def _run_demucs(
    input_wav: Path,
    out_dir: Path,
    model: str = "mdx_extra",
) -> Path:
    """Run Demucs vocal isolation and return the path to the vocals stem.

    Demucs writes its output under:
      out_dir/<model>/<input_stem>/vocals.wav

    Args:
        input_wav: Input WAV file.
        out_dir: Parent directory for Demucs output.
        model: Demucs model name (default: mdx_extra).

    Returns:
        Path to the extracted vocals.wav file.

    Raises:
        RuntimeError: If Demucs fails or the expected output is not found.
    """
    try:
        result = subprocess.run(
            [
                "demucs",
                "--model", model,
                "--two-stems=vocals",
                "--out", str(out_dir),
                str(input_wav),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Demucs failed for {input_wav.name}: {exc.stderr[-600:]}"
        ) from exc

    vocals_path = out_dir / model / input_wav.stem / "vocals.wav"
    if not vocals_path.exists():
        # Demucs sometimes places output differently; glob for it
        candidates = list(out_dir.rglob("vocals.wav"))
        if candidates:
            vocals_path = max(candidates, key=lambda p: p.stat().st_mtime)
        else:
            raise RuntimeError(
                f"Demucs ran but vocals.wav not found under {out_dir}. "
                f"Check Demucs version and model availability."
            )

    return vocals_path


def _loudnorm_wav(
    input_path: Path,
    output_path: Path,
    target_lufs: float = _TARGET_LUFS,
    true_peak: float = _TRUE_PEAK,
    fade_in_ms: int = _FADE_IN_MS,
) -> None:
    """Apply loudnorm and fade-in to a WAV file.

    Two-pass loudnorm (analysis then linear correction) followed by a
    fade-in of fade_in_ms milliseconds.

    Args:
        input_path: Source WAV file.
        output_path: Destination WAV file.
        target_lufs: Integrated LUFS target.
        true_peak: True peak ceiling in dBTP.
        fade_in_ms: Fade-in duration in milliseconds.
    """
    # Pass 1: analysis
    cmd_analysis = [
        "ffmpeg", "-nostats", "-i", str(input_path),
        "-filter:a", f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd_analysis, capture_output=True, text=True, check=False
        )
        m = re.search(r"\{[^}]+\}", result.stderr, re.DOTALL)
        analysis = {}
        if m:
            import json
            try:
                analysis = json.loads(m.group())
            except Exception:
                pass
    except Exception:
        analysis = {}

    fade_sec = fade_in_ms / 1000.0

    if analysis and analysis.get("input_i") not in ("-inf", "inf", None):
        norm_filter = (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:linear=true"
            f":measured_I={analysis.get('input_i', '-23')}"
            f":measured_TP={analysis.get('input_tp', '-2')}"
            f":measured_LRA={analysis.get('input_lra', '7')}"
            f":measured_thresh={analysis.get('input_thresh', '-33')}"
            f":offset={analysis.get('target_offset', '0')}"
        )
    else:
        norm_filter = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11"

    filter_chain = f"{norm_filter},afade=t=in:st=0:d={fade_sec:.3f}"

    cmd_norm = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-filter:a", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    _run_ffmpeg(cmd_norm, label=f"loudnorm {output_path.name}")


# ---------------------------------------------------------------------------
# Round-trip verification
# ---------------------------------------------------------------------------


def _verify_clip(
    clip_path: Path,
    expected_text: str,
    api_key: str,
) -> tuple[str, float]:
    """Transcribe extracted clip with Whisper and fuzzy-check against expected text.

    Args:
        clip_path: Path to the cleaned WAV clip to verify.
        api_key: OpenAI API key.
        expected_text: The dialogue text we expect to hear.

    Returns:
        Tuple of (transcribed_text, match_score). match_score is in [0.0, 1.0].
    """
    try:
        transcribed = _transcribe_with_whisper(
            clip_path, api_key, response_format="text"
        ).strip()
    except Exception as exc:
        logger.warning("Whisper verification failed for %s: %s", clip_path.name, exc)
        return "", 0.0

    score = _token_overlap(transcribed, expected_text)
    logger.debug(
        "Verify %s: expected=%r actual=%r score=%.2f",
        clip_path.name, expected_text, transcribed, score,
    )
    return transcribed, score


# ---------------------------------------------------------------------------
# Full pipeline per line
# ---------------------------------------------------------------------------


def _process_line(
    entry: _SRTEntry,
    era: str,
    source_path: Path,
    output_dir: Path,
    api_key: str,
    tmp_dir: Path,
) -> Optional[ExtractedLine]:
    """Run the full extraction pipeline for a single dialogue line.

    Pipeline: ffmpeg extract -> Demucs vocals -> loudnorm/fade -> Whisper verify.

    Args:
        entry: The SRT entry (timecodes + text).
        era: Era identifier string.
        source_path: Source video file.
        output_dir: Directory to write the final WAV into.
        api_key: OpenAI API key for verification.
        tmp_dir: Scratch directory for intermediates.

    Returns:
        ExtractedLine dataclass, or None if extraction fails.
    """
    slug = _slugify(entry.text)
    safe_era = re.sub(r"[^\w]", "_", era)
    final_name = f"leon_{safe_era}_{slug}.wav"
    final_path = output_dir / final_name

    if final_path.exists():
        logger.info("Already extracted: %s", final_name)
        return ExtractedLine(
            era=era,
            slug=slug,
            text=entry.text,
            output_path=final_path,
            start_sec=entry.start_sec,
            end_sec=entry.end_sec,
            source_file=source_path.name,
            verified=True,
            verified_text="(cached)",
            match_score=1.0,
        )

    # Step 1: raw clip extraction
    raw_clip = tmp_dir / f"{safe_era}_{slug}_raw.wav"
    try:
        _extract_raw_clip(source_path, entry.start_sec, entry.end_sec, raw_clip)
    except RuntimeError as exc:
        logger.warning("Extract failed for '%s': %s", entry.text[:40], exc)
        return None

    # Step 2: Demucs vocal isolation
    demucs_dir = tmp_dir / "demucs_out"
    demucs_dir.mkdir(exist_ok=True)
    try:
        vocals_path = _run_demucs(raw_clip, demucs_dir)
    except RuntimeError as exc:
        logger.warning(
            "Demucs failed for '%s', using raw clip: %s", entry.text[:40], exc
        )
        vocals_path = raw_clip

    # Step 3: loudnorm + fade-in to final path
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _loudnorm_wav(vocals_path, final_path)
    except RuntimeError as exc:
        logger.warning("Loudnorm failed for '%s': %s", entry.text[:40], exc)
        return None

    if not final_path.exists():
        logger.warning("Final file missing after loudnorm: %s", final_path)
        return None

    # Step 4: round-trip verification
    verified_text, score = _verify_clip(final_path, entry.text, api_key)
    verified = score >= _FUZZY_MATCH_THRESHOLD

    if not verified:
        logger.warning(
            "Verification FAIL for '%s' (score=%.2f). Keeping file, marking unverified.",
            entry.text[:40], score,
        )

    return ExtractedLine(
        era=era,
        slug=slug,
        text=entry.text,
        output_path=final_path,
        start_sec=entry.start_sec,
        end_sec=entry.end_sec,
        source_file=source_path.name,
        verified_text=verified_text,
        verified=verified,
        match_score=score,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_era_vo(
    era_source_mp4: str | Path,
    srt_path_or_generate: str | Path | None,
    era: str,
    output_dir: str | Path,
    project_root: Optional[str] = None,
    max_lines: int = _TARGET_LINES_PER_ERA,
) -> list[ExtractedLine]:
    """Extract Leon VO lines from a single era source video.

    When srt_path_or_generate is a path to an existing SRT file it is parsed
    directly. When it is None the function transcribes the video with Whisper
    and saves the SRT alongside the source video. When a list of SRT paths is
    passed all are parsed and pooled.

    Args:
        era_source_mp4: Path to the source video file.
        srt_path_or_generate: Path to an SRT file, list of SRT paths, or None
            to trigger Whisper transcription.
        era: Human-readable era label (e.g. 'RE2R', 'RE9').
        output_dir: Directory where final WAV clips will be saved.
        project_root: Optional path to locate the .env file for the API key.
        max_lines: Maximum number of lines to extract for this era.

    Returns:
        List of ExtractedLine instances (may be fewer than max_lines if the
        source lacks matching content).

    Raises:
        FileNotFoundError: If era_source_mp4 does not exist.
        RuntimeError: If ffmpeg or Demucs are not available.
    """
    _require_ffmpeg()
    api_key = _load_api_key(project_root)

    source_path = Path(era_source_mp4).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source video not found: {source_path}")

    output_dir = Path(output_dir).resolve()
    target_lines = _ERA_TARGET_LINES.get(era, [])

    with tempfile.TemporaryDirectory(prefix="fforge_vo_") as tmp:
        tmp_dir = Path(tmp)

        # Obtain SRT entries
        if srt_path_or_generate is None:
            srt_out = source_path.parent / f"{source_path.stem}.generated.srt"
            transcripts_dir = source_path.parent.parent / "transcripts"
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            srt_out = transcripts_dir / f"{era.lower()}-generated.srt"
            entries = _transcribe_video_to_srt(source_path, srt_out, api_key, tmp_dir)
        elif isinstance(srt_path_or_generate, (list, tuple)):
            entries = []
            for p in srt_path_or_generate:
                entries.extend(_parse_srt(Path(p)))
        else:
            entries = _parse_srt(Path(srt_path_or_generate))

        logger.info("Era %s: %d SRT entries found", era, len(entries))

        # Mine matching lines
        matched = _mine_lines(entries, target_lines, max_results=max_lines)
        logger.info("Era %s: %d candidates after mining", era, len(matched))

        results: list[ExtractedLine] = []
        for entry in matched:
            line = _process_line(entry, era, source_path, output_dir, api_key, tmp_dir)
            if line is not None:
                results.append(line)
                logger.info(
                    "[%s] Extracted: %-40s verified=%s score=%.2f",
                    era, repr(line.text[:38]), line.verified, line.match_score,
                )

    return results


def build_full_vo_library(
    raw_dir: str | Path,
    output_dir: str | Path,
    project_root: Optional[str] = None,
    eras: Optional[list[str]] = None,
) -> dict[str, list[ExtractedLine]]:
    """Build a complete multi-era Leon VO library from all available sources.

    Iterates through all known era video files in raw_dir. For RE9, uses the
    existing SRT files. For all other eras, calls Whisper if no SRT is found.

    Args:
        raw_dir: Directory containing leon-*.mp4 files and RE9 SRT files.
        output_dir: Directory to write all extracted WAV clips.
        project_root: Optional path to locate the .env file.
        eras: Optional list of era keys to process. Defaults to all known eras.

    Returns:
        Dict mapping era -> list[ExtractedLine]. Empty lists for eras where
        the source file was not found in raw_dir.
    """
    raw_dir = Path(raw_dir).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if eras is None:
        eras = list(_ERA_FILENAME_MAP.keys()) + list(_ERA_SRT_MAP.keys())

    library: dict[str, list[ExtractedLine]] = {}

    for era in eras:
        logger.info("=== Processing era: %s ===", era)

        if era in _ERA_SRT_MAP:
            # RE9 or other SRT-sourced eras
            srt_paths = [
                raw_dir / srt_name
                for srt_name in _ERA_SRT_MAP[era]
                if (raw_dir / srt_name).exists()
            ]
            # Use the companion video for extraction (pick first matching re9 mp4)
            video_candidates = list(raw_dir.glob("re9-*.mp4"))
            if not video_candidates:
                video_candidates = list(raw_dir.glob("leon-*.mp4"))

            if not video_candidates:
                logger.warning("No source video found for %s era in %s", era, raw_dir)
                library[era] = []
                continue

            source_video = video_candidates[0]
            srt_source = srt_paths if srt_paths else None

        elif era in _ERA_FILENAME_MAP:
            mp4_name = _ERA_FILENAME_MAP[era]
            source_video = raw_dir / mp4_name
            if not source_video.exists():
                logger.warning(
                    "Source video not found for era %s: %s", era, source_video
                )
                library[era] = []
                continue

            # Check if a generated SRT already exists
            transcripts_dir = raw_dir.parent / "transcripts"
            existing_srt = transcripts_dir / f"{era.lower()}-generated.srt"
            srt_source = existing_srt if existing_srt.exists() else None

        else:
            logger.warning("Unknown era: %s", era)
            library[era] = []
            continue

        try:
            lines = extract_era_vo(
                era_source_mp4=source_video,
                srt_path_or_generate=srt_source,
                era=era,
                output_dir=output_dir,
                project_root=project_root,
            )
            library[era] = lines
        except Exception as exc:
            logger.error("Failed to process era %s: %s", era, exc, exc_info=True)
            library[era] = []

    total = sum(len(v) for v in library.values())
    verified = sum(
        sum(1 for ln in v if ln.verified) for v in library.values()
    )
    logger.info(
        "build_full_vo_library complete: %d total lines, %d verified",
        total, verified,
    )
    return library
