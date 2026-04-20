"""Dialogue search (Phase 6.2) — semantic + phonetic match across ingested
transcripts to find candidate snippets for each script line.

Heuristic-first design:
  - Semantic match: word-overlap Jaccard + bigram overlap (no LLM/embedding
    call yet; that's a Phase 8 upgrade behind a content-hash cache).
  - Phonetic match: simple soundex-like consonant signature comparison.
  - Voice-register match: derived from clip-metadata's audio_type +
    dialogue_clarity_score when available.
  - Speaker-gender match: deferred (needs voice analysis); always neutral.
  - Audio-clarity: pulls dialogue_clarity_score from the candidate clip
    metadata.

For each script line returns the top-K candidate (source_id, start_sec,
end_sec, score) tuples for downstream lipsync + placement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DialogueCandidate:
    line_index: int
    source_id: str
    start_sec: float
    end_sec: float
    transcript_text: str
    semantic_score: float
    phonetic_score: float
    audio_clarity_score: float
    composite_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_index": self.line_index,
            "source_id": self.source_id,
            "start_sec": round(self.start_sec, 3),
            "end_sec": round(self.end_sec, 3),
            "transcript_text": self.transcript_text,
            "semantic_score": round(self.semantic_score, 3),
            "phonetic_score": round(self.phonetic_score, 3),
            "audio_clarity_score": round(self.audio_clarity_score, 1),
            "composite_score": round(self.composite_score, 3),
        }


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z']+", s.lower()))


def _bigrams(s: str) -> set[tuple[str, str]]:
    toks = re.findall(r"[a-z']+", s.lower())
    return set(zip(toks[:-1], toks[1:]))


def _semantic_score(query: str, candidate: str) -> float:
    qt, ct = _tokens(query), _tokens(candidate)
    if not qt or not ct:
        return 0.0
    jaccard = len(qt & ct) / len(qt | ct)
    qb, cb = _bigrams(query), _bigrams(candidate)
    bigram = len(qb & cb) / max(1, len(qb | cb)) if (qb or cb) else 0
    # Weighted: bigrams matter more than single-word overlap
    return 0.4 * jaccard + 0.6 * bigram


def _phonetic_signature(s: str) -> str:
    """Strip vowels + collapse runs — crude phonetic key."""
    consonants = re.sub(r"[aeiouy\s']", "", s.lower())
    out: list[str] = []
    prev = ""
    for c in consonants:
        if c.isalpha() and c != prev:
            out.append(c)
        prev = c
    return "".join(out)


def _phonetic_score(query: str, candidate: str) -> float:
    qs, cs = _phonetic_signature(query), _phonetic_signature(candidate)
    if not qs or not cs:
        return 0.0
    # Longest common substring ratio
    longest = 0
    for i in range(len(qs)):
        for j in range(len(cs)):
            k = 0
            while i + k < len(qs) and j + k < len(cs) and qs[i + k] == cs[j + k]:
                k += 1
            longest = max(longest, k)
    return longest / max(len(qs), len(cs))


def _build_utterances(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Group whisper words into sentence-ish utterances (sliding 4-word
    window with min 0.4s gap as a break)."""
    words = transcript.get("words") or []
    if not words:
        return []
    utterances: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if not w.get("text"):
            continue
        if cur:
            gap = float(w.get("start_sec", 0)) - float(cur[-1].get("end_sec", 0))
            if gap > 0.4 or len(cur) >= 12:
                utterances.append({
                    "text": " ".join(x["text"] for x in cur),
                    "start_sec": float(cur[0].get("start_sec", 0)),
                    "end_sec": float(cur[-1].get("end_sec", 0)),
                })
                cur = []
        cur.append(w)
    if cur:
        utterances.append({
            "text": " ".join(x["text"] for x in cur),
            "start_sec": float(cur[0].get("start_sec", 0)),
            "end_sec": float(cur[-1].get("end_sec", 0)),
        })
    return utterances


def search_for_line(
    line: dict[str, Any],
    transcripts: dict[str, dict[str, Any]],
    *,
    top_k: int = 5,
    fandom_constraint: str | None = None,
) -> list[DialogueCandidate]:
    """Find the top-K candidate utterances matching a script line."""
    target = line.get("text", "")
    line_idx = int(line.get("index", 0))
    target_dur_ms = int(line.get("target_duration_ms", 1500))
    candidates: list[DialogueCandidate] = []
    constraint = fandom_constraint or line.get("fandom_constraint")
    for source_id, transcript in transcripts.items():
        if constraint and constraint.lower() not in source_id.lower():
            continue
        utterances = _build_utterances(transcript)
        for u in utterances:
            text = u["text"]
            if not text:
                continue
            sem = _semantic_score(target, text)
            phon = _phonetic_score(target, text)
            # Audio clarity proxy — words have confidence?
            confs = []
            for w in transcript.get("words") or []:
                if u["start_sec"] <= float(w.get("start_sec", 0)) < u["end_sec"]:
                    if "confidence" in w:
                        confs.append(float(w["confidence"]))
            clarity = (sum(confs) / len(confs) * 100) if confs else 60.0
            # Duration match: penalize utterances too far from target
            actual_ms = (u["end_sec"] - u["start_sec"]) * 1000
            dur_ratio = min(actual_ms, target_dur_ms) / max(actual_ms, target_dur_ms, 1)
            composite = 0.5 * sem + 0.2 * phon + 0.2 * (clarity / 100) + 0.1 * dur_ratio
            if composite > 0.05:  # noise floor
                candidates.append(DialogueCandidate(
                    line_index=line_idx,
                    source_id=source_id,
                    start_sec=u["start_sec"],
                    end_sec=u["end_sec"],
                    transcript_text=text,
                    semantic_score=sem,
                    phonetic_score=phon,
                    audio_clarity_score=clarity,
                    composite_score=composite,
                ))
    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    return candidates[:top_k]


def search_script(
    script: dict[str, Any],
    transcripts: dict[str, dict[str, Any]],
    *,
    top_k: int = 5,
) -> dict[str, list[DialogueCandidate]]:
    """For every line in a script, search transcripts. Returns
    {line_index: [candidates]}."""
    out: dict[str, list[DialogueCandidate]] = {}
    for line in script.get("lines") or []:
        out[str(line.get("index", 0))] = search_for_line(line, transcripts, top_k=top_k)
    return out


def load_transcripts(project_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all whisper transcripts from a project."""
    out: dict[str, dict[str, Any]] = {}
    tdir = project_dir / "data" / "transcripts"
    if not tdir.exists():
        return out
    for p in tdir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sid = p.stem
            out[sid] = data
        except (json.JSONDecodeError, OSError):
            continue
    return out


__all__ = [
    "DialogueCandidate",
    "search_for_line",
    "search_script",
    "load_transcripts",
]
