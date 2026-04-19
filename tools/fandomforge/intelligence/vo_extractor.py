"""Smart VO extraction — ASR-driven filler/disfluency/repetition removal.

Replaces the naive keyword-scored VTT picker with a proper speech-rough-cut
pipeline inspired by FireRed-OpenStoryline's speech_rough_cut node. The
intelligence is rule-based (fast, deterministic, zero API cost) rather than
FireRed's per-sentence LLM call. An optional LLM-assist pass is provided for
cases where pure rules aren't sharp enough.

Pipeline:
    1. Parse VTT/SRT → per-word timestamps (collapse karaoke rolling cues).
    2. Merge words into sentence-like segments.
    3. Detect fillers ("um", "uh", "you know") and remove them with timestamp
       adjustment.
    4. Detect word-level repetitions ("we we", "I I") and collapse.
    5. Detect whole-sentence fillers and drop.
    6. Split a sentence into multiple output segments when a MID-sentence
       deletion occurs (FireRed's key insight — prevents stitching unrelated
       fragments).
    7. Score each clean segment against narrative priorities + punchiness.
    8. Pick top-N, cut wav via ffmpeg with loudnorm, verify via Whisper.

Written to be reusable across projects — no character-specific logic lives
here. Character context is a parameter on every public entry point.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Filler + disfluency vocabulary
# ---------------------------------------------------------------------------

# Single-word fillers. Case-insensitive. Punctuation stripped before match.
_FILLERS: frozenset[str] = frozenset({
    # Classic English fillers
    "um", "uh", "uhh", "umm", "er", "ehm", "hmm", "mhm",
    # Hedges that aren't adding content
    "like", "basically", "actually", "literally", "honestly",
    # Discourse markers when isolated
    "so", "well", "right", "okay", "ok",
    # Repeated affirmations
    "yeah", "yes", "mhm", "mmhm",
})

# Multi-word filler phrases (checked after single-word pass).
_FILLER_PHRASES: tuple[str, ...] = (
    "you know",
    "i mean",
    "kind of",
    "sort of",
    "or something",
    "or whatever",
    "i guess",
    "let me see",
    "let's see",
    "i don't know",
)

# Words we never remove even if they look like fillers.
# Important for character dialogue where "yeah" or "ok" is the actual line.
_KEEP_IF_STANDALONE: frozenset[str] = frozenset({
    # If the ENTIRE sentence is one of these, keep it — it's the line.
    "yeah", "yes", "no", "ok", "okay", "right", "now", "go",
})

# VTT/SRT noise
_VTT_BRACKET_NOISE: tuple[str, ...] = (
    "music", "applause", "laughter", "silence", "cheering",
    "instrumental", "sighs", "gunshot", "screams",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Word:
    """A single word with millisecond timestamps."""
    text: str
    start_ms: int
    end_ms: int

    @property
    def norm(self) -> str:
        """Lowercase, strip leading/trailing punctuation for matching."""
        return re.sub(r"^[^\w]+|[^\w]+$", "", self.text.lower())


@dataclass
class Segment:
    """A candidate line (post filler/repetition cleanup)."""
    start_ms: int
    end_ms: int
    text: str
    words: list[Word] = field(default_factory=list)
    source_stem: str = ""
    score: float = 0.0
    reason: str = ""

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def duration_sec(self) -> float:
        return self.duration_ms / 1000.0


@dataclass
class ExtractionResult:
    """Full output of an extraction run."""
    kept: list[Segment] = field(default_factory=list)
    dropped: list[tuple[Segment, str]] = field(default_factory=list)
    transcript_map: dict[str, str] = field(default_factory=dict)
    source_map: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# VTT parsing (with karaoke rolling-cue collapse)
# ---------------------------------------------------------------------------

_TS_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)
_WORD_TS_RE = re.compile(r"<(\d{2}):(\d{2}):(\d{2})\.(\d{3})>")


def _ts_to_ms(h: int, m: int, s: int, ms: int) -> int:
    return (h * 3600 + m * 60 + s) * 1000 + ms


def parse_vtt_words(vtt: Path) -> list[Word]:
    """Extract word-level timestamps from a YouTube karaoke-style VTT.

    YouTube's auto-subs include inline `<HH:MM:SS.mmm>` markers between words
    giving per-word timing. We use those when present; when absent we fall
    back to cue-level timing and divide evenly across words.
    """
    if not vtt.exists():
        return []
    raw = vtt.read_text(encoding="utf-8", errors="ignore").splitlines()

    words: list[Word] = []
    i = 0
    seen_text_windows: set[tuple[int, str]] = set()

    while i < len(raw):
        m = _TS_RE.match(raw[i].strip())
        if not m:
            i += 1
            continue
        cue_start = _ts_to_ms(int(m[1]), int(m[2]), int(m[3]), int(m[4]))
        cue_end = _ts_to_ms(int(m[5]), int(m[6]), int(m[7]), int(m[8]))
        i += 1

        body_lines: list[str] = []
        while i < len(raw) and raw[i].strip() != "":
            body_lines.append(raw[i])
            i += 1

        # YouTube auto-subs format: each word-timed cue has a plain "settled"
        # line (previous completion), an empty line, and the karaoke line
        # with `<HH:MM:SS.mmm>` inline markers. Only the karaoke line gives
        # us real per-word timing — use that, drop the rest to avoid dupes.
        karaoke_lines = [ln for ln in body_lines if _WORD_TS_RE.search(ln)]
        if not karaoke_lines:
            # A bare cue (settled-only) — we already have its words from an
            # earlier karaoke cue, so skip. When the VTT has NO karaoke
            # markers at all (e.g. hand-crafted subtitles), handle that in
            # a second pass below.
            continue

        body = " ".join(karaoke_lines)
        body = re.sub(r"\[[^\]]+\]", "", body)
        body = re.sub(r"&[a-z_#0-9]+;", " ", body, flags=re.I)

        cue_words = _extract_cue_words(body, cue_start, cue_end)
        if not cue_words:
            continue

        # Karaoke collapse: the same word can appear across rolling cues
        # with slightly different start_ms offsets. Dedupe by (word-norm,
        # start_ms bucketed to 100 ms) so consecutive identical hits merge.
        for w in cue_words:
            if not w.norm:
                continue
            key = (w.norm, w.start_ms // 100)
            if key in seen_text_windows:
                continue
            seen_text_windows.add(key)
            words.append(w)

    if not words:
        # VTT has no karaoke markers at all — treat as plain subtitle file
        # with one sentence per cue. Divide cue span evenly across tokens.
        words = _fallback_plain_vtt(vtt)

    words.sort(key=lambda w: w.start_ms)
    return words


def _fallback_plain_vtt(vtt: Path) -> list[Word]:
    """Parse a VTT without inline word-level timestamps (plain subs)."""
    raw = vtt.read_text(encoding="utf-8", errors="ignore").splitlines()
    words: list[Word] = []
    i = 0
    while i < len(raw):
        m = _TS_RE.match(raw[i].strip())
        if not m:
            i += 1
            continue
        cue_start = _ts_to_ms(int(m[1]), int(m[2]), int(m[3]), int(m[4]))
        cue_end = _ts_to_ms(int(m[5]), int(m[6]), int(m[7]), int(m[8]))
        i += 1
        body_lines: list[str] = []
        while i < len(raw) and raw[i].strip() != "":
            body_lines.append(raw[i])
            i += 1
        body = " ".join(body_lines)
        body = re.sub(r"<[^>]+>|\[[^\]]+\]|&[a-z_#0-9]+;", " ", body, flags=re.I)
        toks = [t for t in body.split() if t.lower() not in _VTT_BRACKET_NOISE]
        if not toks:
            continue
        span = max(1, cue_end - cue_start)
        per = span // len(toks)
        for j, t in enumerate(toks):
            s = cue_start + j * per
            e = s + per if j < len(toks) - 1 else cue_end
            words.append(Word(text=t, start_ms=s, end_ms=e))
    return words


def _extract_cue_words(body: str, cue_start: int, cue_end: int) -> list[Word]:
    """Parse a single VTT cue body into per-word Word records."""
    body = body.replace("\u00a0", " ").strip()
    if not body:
        return []

    # Split on inline word-timestamp markers; each chunk is a word-or-phrase
    # that begins at the preceding timestamp (or cue_start for the first).
    parts = re.split(r"(<\d{2}:\d{2}:\d{2}\.\d{3}>)", body)
    # Walk parts collecting (start_ms, text) pairs.
    words: list[Word] = []
    current_start = cue_start
    buffer_text = ""

    def flush(start_ms: int, end_ms: int, text: str) -> None:
        text = re.sub(r"<[^>]+>", "", text)
        text = text.strip()
        if not text:
            return
        for tok in text.split():
            if tok.lower() in _VTT_BRACKET_NOISE:
                continue
            words.append(Word(text=tok, start_ms=start_ms, end_ms=end_ms))

    for part in parts:
        mw = _WORD_TS_RE.match(part)
        if mw:
            next_start = _ts_to_ms(int(mw[1]), int(mw[2]), int(mw[3]), int(mw[4]))
            if buffer_text:
                flush(current_start, next_start, buffer_text)
                buffer_text = ""
            current_start = next_start
            continue
        buffer_text += " " + part
    if buffer_text:
        flush(current_start, cue_end, buffer_text)

    # If we ended up with a single-chunk cue (no inline markers), re-split
    # the sentence across the cue window uniformly.
    if len(words) == 0 and body:
        toks = [
            t for t in body.split()
            if t.lower() not in _VTT_BRACKET_NOISE
        ]
        if toks:
            span = max(1, cue_end - cue_start)
            per = span // len(toks)
            for j, t in enumerate(toks):
                s = cue_start + j * per
                e = s + per if j < len(toks) - 1 else cue_end
                words.append(Word(text=t, start_ms=s, end_ms=e))

    return words


# ---------------------------------------------------------------------------
# Filler / repetition cleaning
# ---------------------------------------------------------------------------

def clean_words(words: list[Word]) -> tuple[list[Word], list[tuple[int, int, str]]]:
    """Apply filler + repetition removal. Returns (cleaned_words, deleted_ranges).

    deleted_ranges is a list of (start_ms, end_ms, reason) for every removed
    span — callers can use this to decide whether a mid-word deletion forces
    a segment split (FireRed pattern).
    """
    if not words:
        return [], []

    deleted: list[tuple[int, int, str]] = []
    kept: list[Word] = []

    # Pass 1: multi-word filler phrases
    phrase_ranges: list[tuple[int, int, str]] = []
    i = 0
    while i < len(words):
        matched = False
        for phrase in _FILLER_PHRASES:
            tokens = phrase.split()
            if i + len(tokens) <= len(words):
                window = " ".join(w.norm for w in words[i:i + len(tokens)])
                if window == phrase:
                    phrase_ranges.append(
                        (words[i].start_ms, words[i + len(tokens) - 1].end_ms,
                         f"filler-phrase:{phrase}")
                    )
                    i += len(tokens)
                    matched = True
                    break
        if not matched:
            i += 1
    phrase_idx = set()
    for start, end, _ in phrase_ranges:
        for j, w in enumerate(words):
            if start <= w.start_ms < end:
                phrase_idx.add(j)

    # Pass 2: single-word fillers (with keep-if-alone guard)
    single_filler_idx: set[int] = set()
    total_non_filler = sum(
        1 for j, w in enumerate(words)
        if j not in phrase_idx and w.norm not in _FILLERS
    )
    for j, w in enumerate(words):
        if j in phrase_idx:
            continue
        n = w.norm
        if not n:
            single_filler_idx.add(j)
            continue
        if n in _FILLERS:
            # If removing this leaves an empty sentence, keep it — single-word
            # "yeah" or "ok" might be the actual character line.
            if total_non_filler <= 1 and n in _KEEP_IF_STANDALONE:
                continue
            single_filler_idx.add(j)

    # Pass 3: consecutive-word repetitions (we we, I I, I- I)
    repetition_idx: set[int] = set()
    prev_norm: str | None = None
    for j, w in enumerate(words):
        if j in phrase_idx or j in single_filler_idx:
            continue
        n = w.norm
        if prev_norm and n == prev_norm and len(n) <= 4:
            repetition_idx.add(j)
        else:
            prev_norm = n

    # Emit cleaned word list + ranges
    for j, w in enumerate(words):
        if j in phrase_idx:
            deleted.append((w.start_ms, w.end_ms, "filler-phrase"))
            continue
        if j in single_filler_idx:
            deleted.append((w.start_ms, w.end_ms, f"filler:{w.norm}"))
            continue
        if j in repetition_idx:
            deleted.append((w.start_ms, w.end_ms, "repetition"))
            continue
        kept.append(w)

    return kept, deleted


# ---------------------------------------------------------------------------
# Segmentation (sentence boundaries + FireRed-style split on mid-deletion)
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]+$")


def segment_words(
    words: list[Word],
    deleted_ranges: list[tuple[int, int, str]],
    *,
    max_gap_ms: int = 450,
    min_words: int = 2,
    max_words: int = 18,
) -> list[Segment]:
    """Group cleaned words into sentences; split on (a) punctuation, (b) long
    gaps, (c) mid-sentence deletions that leave disjoint chunks."""
    if not words:
        return []

    # Find all deletion gaps that sit BETWEEN kept words and are longer than
    # max_gap_ms — those force a split.
    def is_forced_split(prev_end: int, next_start: int) -> bool:
        for ds, de, _ in deleted_ranges:
            if prev_end <= ds and de <= next_start:
                if de - ds >= 250:
                    return True
        return next_start - prev_end >= max_gap_ms

    segments: list[Segment] = []
    cur: list[Word] = [words[0]]

    for i in range(1, len(words)):
        prev = words[i - 1]
        nxt = words[i]
        forced = is_forced_split(prev.end_ms, nxt.start_ms)
        hit_punct = bool(_SENTENCE_END_RE.search(prev.text))
        too_long = len(cur) >= max_words

        if forced or hit_punct or too_long:
            if len(cur) >= min_words:
                segments.append(_words_to_segment(cur))
            cur = [nxt]
        else:
            cur.append(nxt)

    if len(cur) >= min_words:
        segments.append(_words_to_segment(cur))

    return segments


def _words_to_segment(ws: list[Word]) -> Segment:
    text = " ".join(w.text for w in ws)
    return Segment(
        start_ms=ws[0].start_ms,
        end_ms=ws[-1].end_ms,
        text=text,
        words=ws,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_segment(
    seg: Segment,
    *,
    narrative_priorities: list[str],
    min_duration_sec: float = 0.7,
    max_duration_sec: float = 5.5,
    ideal_words: tuple[int, int] = (4, 12),
) -> float:
    """Score a segment. Higher is better."""
    dur = seg.duration_sec
    if dur < min_duration_sec or dur > max_duration_sec:
        return -1.0
    n_words = len(seg.words)
    txt = seg.text.lower()
    score = 0.0
    # Narrative priority matches (per phrase, counted once)
    for p in narrative_priorities:
        if p.lower() in txt:
            score += 2.0
    # Punchiness sweet spot
    if ideal_words[0] <= n_words <= ideal_words[1]:
        score += 1.5
    elif n_words < ideal_words[0]:
        score += 0.5
    # Penalize if sentence ends mid-thought (tail word is a conjunction,
    # preposition, or article — classic sign of a truncated VTT cue that
    # the karaoke parser cut in the middle of a clause).
    tail = seg.words[-1].norm if seg.words else ""
    if tail in {
        "and", "but", "or", "so", "because", "that", "which", "if",
        "while", "when", "who", "whose", "whom",
        "the", "a", "an",
        "to", "of", "in", "on", "at", "for", "with", "by", "from",
        "into", "onto", "over", "under", "than", "perhaps",
        "i", "you", "we", "they", "he", "she", "it",
        "is", "are", "was", "were", "be", "am",
    }:
        score -= 1.5
    # Also penalize head-word oddities — segments starting with a pronoun
    # subject stripped of its verb tend to read incomplete.
    head = seg.words[0].norm if seg.words else ""
    if head in {"and", "but", "or", "so", "because", "that"}:
        score -= 0.5
    # Bonus for complete-thought endings
    if _SENTENCE_END_RE.search(seg.text):
        score += 0.6
    # Bonus for common sentence-starter patterns (natural hook)
    if len(seg.words) >= 3:
        head_bi = " ".join(w.norm for w in seg.words[:2])
        if head_bi in {"i'm", "i am", "we are", "we're", "it's", "you're",
                       "we don't", "i don't", "he's", "she's"}:
            score += 0.4
    return score


# ---------------------------------------------------------------------------
# ffmpeg cut + Whisper verify
# ---------------------------------------------------------------------------

def cut_wav(
    source_video: Path,
    seg: Segment,
    out_wav: Path,
    *,
    preroll_sec: float = 0.30,
    tail_pad_sec: float = 0.15,
    target_lufs: float = -14.0,
    fade_in_ms: int = 15,
) -> bool:
    """ffmpeg-cut a mono 48k wav with loudnorm + fade-in."""
    if shutil.which("ffmpeg") is None:
        return False
    start_sec = max(0.0, seg.start_ms / 1000.0 - preroll_sec)
    dur_sec = (seg.end_ms - seg.start_ms) / 1000.0 + preroll_sec + tail_pad_sec
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", str(source_video),
        "-t", f"{dur_sec:.3f}",
        "-vn",
        "-af",
        f"afade=t=in:st=0:d={fade_in_ms / 1000.0:.3f},"
        f"loudnorm=I={target_lufs}:TP=-1:LRA=7",
        "-ar", "48000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return out_wav.exists() and out_wav.stat().st_size > 2000


def whisper_verify(
    wav: Path,
    expected: str,
    *,
    openai_api_key: str | None = None,
    min_overlap: float = 0.40,
) -> tuple[bool, str]:
    """Round-trip the wav through Whisper; return (passed, transcribed)."""
    if openai_api_key is None:
        import os
        openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        return True, expected  # no key, trust the extraction
    try:
        from fandomforge.intelligence.openai_helper import transcribe_via_openai
    except ImportError:
        return True, expected
    out_srt = wav.with_suffix(".verify.srt")
    transcribe_via_openai(wav, out_srt, project_root=str(Path.cwd()))
    transcribed = ""
    if out_srt.exists():
        raw = out_srt.read_text()
        raw = re.sub(r"\d+\n|\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\n", "", raw)
        transcribed = re.sub(r"\s+", " ", raw).strip()
        out_srt.unlink(missing_ok=True)
    exp_tokens = set(re.findall(r"\w+", expected.lower()))
    got_tokens = set(re.findall(r"\w+", transcribed.lower()))
    if not exp_tokens:
        return False, transcribed
    overlap = len(exp_tokens & got_tokens) / max(1, len(exp_tokens))
    return overlap >= min_overlap, transcribed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_vo_from_source(
    source_video: Path,
    source_stem: str,
    era: str,
    output_dir: Path,
    *,
    character: str,
    narrative_priorities: list[str],
    max_lines: int = 12,
    min_duration_sec: float = 0.7,
    max_duration_sec: float = 5.5,
    verify_with_whisper: bool = True,
    dedupe_slug_prefix_len: int = 20,
    isolate_voice: bool = False,
    voice_query: str | None = None,
) -> ExtractionResult:
    """Extract clean VO lines from one source video + VTT pair.

    Returns ExtractionResult with transcript_map + source_map ready to write.
    """
    vtt = source_video.with_suffix(".en.vtt")
    words = parse_vtt_words(vtt)
    if not words:
        # Fall back to Whisper transcription of the whole source
        srt = source_video.with_suffix(".en.srt")
        if not srt.exists():
            try:
                from fandomforge.intelligence.openai_helper import transcribe_via_openai
                transcribe_via_openai(source_video, srt, project_root=str(Path.cwd()))
            except Exception:  # noqa: BLE001
                return ExtractionResult()
        # Rudimentary SRT → Word conversion (cue-uniform word timing)
        words = _parse_srt_as_words(srt)
    if not words:
        return ExtractionResult()

    cleaned, deletions = clean_words(words)
    segments = segment_words(cleaned, deletions)

    # Score + dedupe + pick
    for seg in segments:
        seg.source_stem = source_stem
        seg.score = score_segment(
            seg,
            narrative_priorities=narrative_priorities,
            min_duration_sec=min_duration_sec,
            max_duration_sec=max_duration_sec,
        )

    candidates = [s for s in segments if s.score > 0]
    candidates.sort(key=lambda s: (-s.score, s.start_ms))

    picked: list[Segment] = []
    seen_prefixes: set[str] = set()
    for seg in candidates:
        key = _slugify(seg.text)[:dedupe_slug_prefix_len]
        if key in seen_prefixes:
            continue
        picked.append(seg)
        seen_prefixes.add(key)
        if len(picked) >= max_lines:
            break

    result = ExtractionResult()
    for seg in picked:
        slug = _slugify(seg.text, max_len=40)
        wav_name = f"{character}_{era}_{slug}.wav"
        out_wav = output_dir / wav_name
        if not cut_wav(source_video, seg, out_wav):
            result.dropped.append((seg, "ffmpeg-cut-failed"))
            continue

        # Optional: run voice isolation so only the target speaker's voice
        # remains. This dramatically improves Whisper verification on
        # clips with music or other characters in the background.
        if isolate_voice:
            try:
                from fandomforge.intelligence import voice_isolator
                if voice_isolator.is_available():
                    query = voice_query or "a voice speaking clearly"
                    isolated = out_wav.with_suffix(".isolated.wav")
                    ran = voice_isolator.isolate_voice(
                        out_wav, query, isolated, target_sr=48000,
                    )
                    if ran and isolated.exists() and isolated.stat().st_size > 2000:
                        out_wav.unlink(missing_ok=True)
                        isolated.rename(out_wav)
            except Exception:  # noqa: BLE001
                # Isolation is best-effort; never block extraction on it
                pass

        if verify_with_whisper:
            ok, got = whisper_verify(out_wav, seg.text)
            if not ok:
                out_wav.unlink(missing_ok=True)
                result.dropped.append((seg, f"whisper-verify-failed:{got!r}"))
                continue
        result.kept.append(seg)
        result.transcript_map[wav_name] = seg.text
        result.source_map[wav_name] = {
            "source_mp4": str(source_video),
            "source_start_sec": round(seg.start_ms / 1000.0, 3),
            "source_end_sec": round(seg.end_ms / 1000.0, 3),
            "era": era,
            "score": round(seg.score, 2),
        }

    return result


def extract_vo_library(
    project_dir: Path,
    *,
    character: str,
    era_source_map: dict[str, str],
    narrative_priorities: list[str],
    max_lines_per_source: int = 12,
    verify_with_whisper: bool = True,
    isolate_voice: bool = False,
    voice_query: str | None = None,
) -> ExtractionResult:
    """Run extraction across every source in era_source_map.

    Writes dialogue/*.wav + dialogue/transcript-map.json + dialogue/source-map.json.

    isolate_voice: If True and AudioSep is installed (see voice_isolator.py
        for install notes), each extracted wav is passed through AudioSep
        to strip music / other voices before Whisper verification. Falls
        back gracefully to no-op when AudioSep is absent.
    voice_query: Natural-language description of the voice to keep
        (e.g. "a gruff male voice"). Ignored when isolate_voice is False.
    """
    raw_dir = project_dir / "raw"
    out_dir = project_dir / "dialogue"
    out_dir.mkdir(parents=True, exist_ok=True)

    combined = ExtractionResult()
    for era, stem in era_source_map.items():
        source_mp4 = raw_dir / f"{stem}.mp4"
        if not source_mp4.exists():
            continue
        per_source = extract_vo_from_source(
            source_mp4, stem, era, out_dir,
            character=character,
            narrative_priorities=narrative_priorities,
            max_lines=max_lines_per_source,
            verify_with_whisper=verify_with_whisper,
            isolate_voice=isolate_voice,
            voice_query=voice_query,
        )
        combined.kept.extend(per_source.kept)
        combined.dropped.extend(per_source.dropped)
        combined.transcript_map.update(per_source.transcript_map)
        combined.source_map.update(per_source.source_map)

    (out_dir / "transcript-map.json").write_text(
        json.dumps(combined.transcript_map, indent=2)
    )
    (out_dir / "source-map.json").write_text(
        json.dumps(combined.source_map, indent=2)
    )
    return combined


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"\s+", "-", s.strip())
    return s[:max_len].strip("-")


_SRT_TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _parse_srt_as_words(srt: Path) -> list[Word]:
    """Minimal SRT → Word list with per-cue uniform word timing."""
    if not srt.exists():
        return []
    raw = srt.read_text(encoding="utf-8", errors="ignore").splitlines()
    words: list[Word] = []
    i = 0
    while i < len(raw):
        m = _SRT_TS_RE.search(raw[i])
        if not m:
            i += 1
            continue
        cue_start = _ts_to_ms(int(m[1]), int(m[2]), int(m[3]), int(m[4]))
        cue_end = _ts_to_ms(int(m[5]), int(m[6]), int(m[7]), int(m[8]))
        i += 1
        body_lines: list[str] = []
        while i < len(raw) and raw[i].strip():
            body_lines.append(raw[i])
            i += 1
        body = " ".join(body_lines)
        body = re.sub(r"\[[^\]]+\]", "", body).strip()
        toks = [t for t in body.split() if t.lower() not in _VTT_BRACKET_NOISE]
        if not toks:
            continue
        span = max(1, cue_end - cue_start)
        per = span // len(toks)
        for j, t in enumerate(toks):
            s = cue_start + j * per
            e = s + per if j < len(toks) - 1 else cue_end
            words.append(Word(text=t, start_ms=s, end_ms=e))
    return words
