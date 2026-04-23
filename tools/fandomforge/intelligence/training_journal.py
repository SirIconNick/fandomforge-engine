"""Render training journal — the dataset that lets the engine learn.

Every rendered edit appends a record here. A record captures the
config that was in effect (craft weights, cascade flags, target cpm,
color preset), summary statistics from the produced shot-list (shot
count, avg duration, source diversity), and the review outcome (score
per dimension, grade, tier).

The journal is an append-only JSONL file at
``~/.fandomforge/training/journal.jsonl`` (or ``$FF_TRAINING_JOURNAL``).
Append is atomic per-record — one JSON object per line, fsync on
write, so concurrent autopilot runs don't corrupt the file.

Consumers:

* `outcome_aggregator.py` computes correlation patterns across the
  journal and writes mined training priors
* `ff train status` reads the journal to answer "how many renders has
  the engine learned from, average grade, per-bucket distribution"
* Future A/B experiments use the journal to look up prior outcomes

Records do NOT include raw shot lists or video paths — the journal is
a lightweight learning dataset, not an archive. The project directory
still holds the full artifacts for any render that wants to be
re-examined.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "RenderJournalEntry",
    "append_entry",
    "iter_entries",
    "journal_path",
    "summary",
]


_DEFAULT_JOURNAL = Path.home() / ".fandomforge" / "training" / "journal.jsonl"
_JOURNAL_ENV = "FF_TRAINING_JOURNAL"


def _repo_local_journal() -> Path | None:
    """Walk up from this module to find the repo root and return
    ``<repo>/.cache/ff/training/journal.jsonl``. Returns None when no
    repo root is found (e.g. installed as a package).

    Prefer .git over pyproject.toml so a nested tools/pyproject.toml
    doesn't shadow the real repo root.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent / ".cache" / "ff" / "training" / "journal.jsonl"
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent / ".cache" / "ff" / "training" / "journal.jsonl"
    return None


def _can_write_to(path: Path) -> bool:
    """Check whether we can actually write a file at ``path``.

    mkdir(exist_ok=True) can succeed silently when the dir is already
    present in a sandboxed environment where subsequent file writes are
    still denied, so this does a real write-probe on a sibling temp
    file and removes it. Returns False on any PermissionError or OSError.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        return False
    probe = path.parent / f".ff-write-probe-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except (PermissionError, OSError):
        return False
    return True


@dataclass
class RenderJournalEntry:
    """One record of a rendered edit + its review outcome."""

    project_slug: str
    generated_at: str
    edit_type: str | None = None
    target_duration_sec: float | None = None

    # Configuration fields the engine can vary. These are the
    # "features" the aggregator correlates against review scores.
    mfv_craft_enabled: bool = True
    craft_weights: dict[str, float] = field(default_factory=dict)
    pre_drop_dropout_sec: float = 0.0
    j_cut_lead_sec: float = 0.0
    target_cpm: float | None = None
    color_preset: str | None = None

    # Shot-list summary statistics (what the engine actually produced)
    shot_count: int = 0
    avg_shot_duration_sec: float = 0.0
    source_diversity_entropy: float = 0.0
    num_sources_used: int = 0
    hero_reserved_count: int = 0
    drum_fill_count: int = 0
    lyric_sync_count: int = 0
    dropout_windows_count: int = 0

    # Review outcome (what matters)
    overall_score: float = 0.0
    overall_grade: str = ""
    tier: str = ""
    dim_technical: float = 0.0
    dim_visual: float = 0.0
    dim_audio: float = 0.0
    dim_structural: float = 0.0
    dim_shot_list: float = 0.0
    dim_coherence: float = 0.0
    dim_arc_shape: float = 0.0
    dim_engagement: float = 0.0

    # Provenance
    render_id: str = ""
    review_findings: list[str] = field(default_factory=list)

    # Experiment pairing — when a render is the A or B side of an A/B
    # experiment, both variants share the same experiment_id so the
    # aggregator can weight paired entries higher than observational
    # renders.
    experiment_id: str = ""
    experiment_variant: str = ""  # "A" or "B" — empty when not part of an experiment
    experiment_vary_field: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def journal_path() -> Path:
    """Resolve the journal path from env or default.

    Prefers ``$FF_TRAINING_JOURNAL`` when set. Otherwise tries the user-level
    ``~/.fandomforge/training/journal.jsonl``; if that's unwritable (sandboxed
    environment, etc.) falls back to ``<repo>/.cache/ff/training/journal.jsonl``.
    """
    override = os.environ.get(_JOURNAL_ENV)
    if override:
        return Path(override)
    if _can_write_to(_DEFAULT_JOURNAL):
        return _DEFAULT_JOURNAL
    local = _repo_local_journal()
    if local is not None:
        return local
    return _DEFAULT_JOURNAL


def append_entry(entry: RenderJournalEntry, *, path: Path | None = None) -> Path:
    """Append one entry to the journal as a JSONL line. Atomic per-line."""
    p = Path(path) if path else journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
    # Append + fsync so concurrent writers don't tear each other's lines.
    with p.open("a", encoding="utf-8") as f:
        f.write(payload)
        f.write("\n")
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return p


def iter_entries(
    path: Path | None = None,
    *,
    filter_bucket: str | None = None,
    min_score: float | None = None,
) -> Iterator[RenderJournalEntry]:
    """Yield every entry from the journal (optionally filtered)."""
    p = Path(path) if path else journal_path()
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                entry = RenderJournalEntry(**data)
            except TypeError:
                # Tolerate older records that are missing newer fields by
                # dropping unknown keys and filling missing ones from the
                # dataclass defaults.
                valid_keys = {f.name for f in RenderJournalEntry.__dataclass_fields__.values()}
                data = {k: v for k, v in data.items() if k in valid_keys}
                entry = RenderJournalEntry(**data)
            if filter_bucket is not None and entry.edit_type != filter_bucket:
                continue
            if min_score is not None and entry.overall_score < min_score:
                continue
            yield entry


def summary(path: Path | None = None) -> dict[str, Any]:
    """Compute aggregate stats across the whole journal — how many
    renders, per-bucket grade distribution, average scores, etc."""
    entries = list(iter_entries(path))
    if not entries:
        return {
            "total": 0,
            "message": "no journal entries yet",
            "path": str(path or journal_path()),
        }
    from collections import Counter
    buckets: dict[str, list[float]] = {}
    grades: Counter = Counter()
    dim_means: dict[str, list[float]] = {}
    for e in entries:
        buckets.setdefault(e.edit_type or "unknown", []).append(e.overall_score)
        grades[e.overall_grade or "?"] += 1
        for dim_name in ("technical", "visual", "audio", "structural",
                         "shot_list", "coherence", "arc_shape", "engagement"):
            v = getattr(e, f"dim_{dim_name}", 0.0)
            dim_means.setdefault(dim_name, []).append(v)
    out = {
        "total": len(entries),
        "path": str(path or journal_path()),
        "per_bucket": {
            b: {
                "count": len(scores),
                "avg_score": round(sum(scores) / len(scores), 2),
                "min": round(min(scores), 2),
                "max": round(max(scores), 2),
            }
            for b, scores in buckets.items()
        },
        "grade_distribution": dict(grades),
        "dimension_averages": {
            name: round(sum(vals) / len(vals), 2)
            for name, vals in dim_means.items()
        },
        "overall_avg_score": round(
            sum(e.overall_score for e in entries) / len(entries), 2
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Builder helpers — used by the autopilot wire-in to construct entries
# from on-disk project artifacts without every caller hand-assembling one.
# ---------------------------------------------------------------------------


def build_entry_from_project(
    project_dir: Path,
    *,
    review_report: dict[str, Any] | None = None,
) -> RenderJournalEntry | None:
    """Read a project's data/ artifacts and construct a journal entry.

    Returns None when the required artifacts aren't present — the
    autopilot swallows that and moves on. Never raises.
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        return None
    try:
        intent = _load_json(project_dir / "data" / "intent.json")
        edit_plan = _load_json(project_dir / "data" / "edit-plan.json")
        shot_list = _load_json(project_dir / "data" / "shot-list.json")
        review = review_report or _load_json(project_dir / "data" / "post-render-review.json")
    except Exception:  # noqa: BLE001
        return None
    if review is None:
        return None

    edit_type = None
    if intent:
        edit_type = intent.get("edit_type") or intent.get("type")

    # Project config — use our real loader so we capture mfv_craft_enabled etc.
    craft_enabled = True
    pre_drop_dropout_sec = 0.0
    j_cut_lead_sec = 0.0
    target_duration_sec = None
    try:
        from fandomforge.config import craft_weights_for, load_project_config
        cfg = load_project_config(project_dir)
        craft_enabled = bool(cfg.mfv_craft_enabled)
        pre_drop_dropout_sec = float(cfg.pre_drop_dropout_sec)
        j_cut_lead_sec = float(cfg.j_cut_lead_sec)
        target_duration_sec = cfg.target_duration_sec
        craft_weights = craft_weights_for(edit_type)
    except Exception:  # noqa: BLE001
        craft_weights = {}

    # Shot-list summary
    shots = (shot_list or {}).get("shots") or []
    fps = float((shot_list or {}).get("fps") or 24.0)
    shot_count = len(shots)
    avg_dur = 0.0
    if shot_count:
        total_frames = sum(int(s.get("duration_frames") or 0) for s in shots)
        avg_dur = (total_frames / fps) / shot_count

    sources = [s.get("source_id", "") for s in shots]
    distinct_sources = len({s for s in sources if s})
    hero_count = sum(1 for s in shots if s.get("intent") == "hero_reserved")
    drum_fill_count = sum(
        1 for s in shots
        if (s.get("beat_sync") or {}).get("type") == "drum_fill"
    )
    lyric_count = sum(
        1 for s in shots
        if (s.get("beat_sync") or {}).get("type") == "lyric_sync"
    )

    # Source entropy (same Shannon formula used elsewhere)
    import math
    source_counts: dict[str, int] = {}
    for sid in sources:
        if not sid:
            continue
        source_counts[sid] = source_counts.get(sid, 0) + 1
    total = sum(source_counts.values())
    entropy = 0.0
    if total:
        for n in source_counts.values():
            p = n / total
            if p > 0:
                entropy -= p * math.log(p)

    # Review dimensions
    dims = {d["name"]: d for d in (review.get("dimensions") or [])}
    def _d(name: str) -> float:
        return float(dims.get(name, {}).get("score", 0.0) or 0.0)

    findings: list[str] = []
    for d in (review.get("dimensions") or []):
        for f in d.get("findings") or []:
            findings.append(f"{d.get('name')}: {f}")

    return RenderJournalEntry(
        project_slug=project_dir.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        edit_type=edit_type,
        target_duration_sec=float(target_duration_sec) if target_duration_sec else None,
        mfv_craft_enabled=craft_enabled,
        craft_weights=dict(craft_weights),
        pre_drop_dropout_sec=pre_drop_dropout_sec,
        j_cut_lead_sec=j_cut_lead_sec,
        target_cpm=float((edit_plan or {}).get("target_cpm") or 0.0) or None,
        color_preset="tactical",  # autopilot's default; swap when we support overrides
        shot_count=shot_count,
        avg_shot_duration_sec=round(avg_dur, 3),
        source_diversity_entropy=round(entropy, 3),
        num_sources_used=distinct_sources,
        hero_reserved_count=hero_count,
        drum_fill_count=drum_fill_count,
        lyric_sync_count=lyric_count,
        dropout_windows_count=len(review.get("dropout_windows") or []),
        overall_score=float(review.get("score") or 0.0),
        overall_grade=str(review.get("grade") or ""),
        tier=str(review.get("tier") or ""),
        dim_technical=_d("technical"),
        dim_visual=_d("visual"),
        dim_audio=_d("audio"),
        dim_structural=_d("structural"),
        dim_shot_list=_d("shot_list"),
        dim_coherence=_d("coherence"),
        dim_arc_shape=_d("arc_shape"),
        dim_engagement=_d("engagement"),
        render_id=str(review.get("render_id") or ""),
        review_findings=findings[:20],
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
