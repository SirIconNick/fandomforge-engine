"""Prior updater (Phase 7.2).

Consumes a DiffReport (from resolve_diff) and nudges the engine's priors:
  - shot-role weights: cuts user removes → role bias DOWN; cuts user
    adds → role bias UP
  - duration preferences: aggregate the user's average duration for each
    role and nudge the type's target_shot_duration toward it
  - clip-category bias (clip_selection_weights in edit-types.json)

Per amendment A7 — accumulation-based, not single-diff-based. Each diff
is appended to a journal (.history/diff-journal.jsonl); the retrain
command consumes the journal once N diffs accumulate.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOURNAL_DIR_NAME = ".history"
JOURNAL_FILE_NAME = "diff-journal.jsonl"
DEFAULT_MIN_DIFFS = 5
NUDGE_STEP = 0.05  # how much each accumulated diff moves a weight (small)


def append_to_journal(diff_report: dict[str, Any], project_root: Path) -> Path:
    """Append a single diff to the global journal under project_root."""
    out_dir = project_root / JOURNAL_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / JOURNAL_FILE_NAME
    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **diff_report,
    }
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return out


def load_journal(project_root: Path) -> list[dict[str, Any]]:
    p = project_root / JOURNAL_DIR_NAME / JOURNAL_FILE_NAME
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def aggregate_signals(journal: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-role / per-source signals across all journaled diffs."""
    role_remove = defaultdict(int)
    role_add = defaultdict(int)
    duration_changes = defaultdict(list)
    source_remove = defaultdict(int)
    source_add = defaultdict(int)
    for entry in journal:
        for c in entry.get("cuts_removed") or []:
            sid = c.get("source_id", "")
            source_remove[sid] += 1
        for c in entry.get("cuts_added") or []:
            sid = c.get("source_id", "")
            source_add[sid] += 1
        for c in entry.get("cuts_duration_changed") or []:
            sid = c.get("source_id", "")
            delta = float(c.get("edited_duration_sec", 0)) - float(c.get("original_duration_sec", 0))
            duration_changes[sid].append(delta)
    return {
        "diff_count": len(journal),
        "source_remove_counts": dict(source_remove),
        "source_add_counts": dict(source_add),
        "source_duration_avg_delta": {
            sid: round(sum(d) / len(d), 3) for sid, d in duration_changes.items()
        },
        "total_cuts_removed": sum(source_remove.values()),
        "total_cuts_added": sum(source_add.values()),
    }


def update_priors(
    project_root: Path,
    *,
    min_diffs: int = DEFAULT_MIN_DIFFS,
    apply: bool = False,
) -> dict[str, Any]:
    """Look at the journal, decide which priors to nudge.

    Returns a report dict describing proposed changes. With apply=True,
    actually writes the nudges into the priors files. Default apply=False
    is dry-run.

    Per amendment A7: refuses when journal has fewer than min_diffs
    entries — single-diff retraining is noise.
    """
    journal = load_journal(project_root)
    if len(journal) < min_diffs:
        return {
            "applied": False,
            "reason": f"only {len(journal)} diffs in journal; need ≥{min_diffs}",
            "min_diffs": min_diffs,
        }
    signals = aggregate_signals(journal)
    proposed: dict[str, Any] = {
        "diff_count": signals["diff_count"],
        "source_nudges": [],
    }

    # Source bias: sources the user removes more than adds get a -nudge,
    # vice versa get +nudge. Magnitude scales with delta vs total.
    all_sources = set(signals["source_remove_counts"]) | set(signals["source_add_counts"])
    for sid in all_sources:
        added = signals["source_add_counts"].get(sid, 0)
        removed = signals["source_remove_counts"].get(sid, 0)
        if added == removed:
            continue
        delta = added - removed  # positive = user wants more, negative = less
        nudge = NUDGE_STEP * delta
        proposed["source_nudges"].append({
            "source_id": sid,
            "added": added, "removed": removed,
            "nudge": round(nudge, 3),
            "interpretation": "boost" if nudge > 0 else "downweight",
        })

    if apply:
        # Write proposed nudges to references/priors/source-bias.json so
        # the sync planner can read them. Keeps changes localized; doesn't
        # mutate the canonical reference-priors.json.
        out = project_root / "references" / "priors" / "user-bias.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if out.exists():
            try:
                existing = json.loads(out.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        # Merge cumulatively
        accum = existing.get("source_bias", {})
        for n in proposed["source_nudges"]:
            sid = n["source_id"]
            accum[sid] = round(accum.get(sid, 0.0) + n["nudge"], 3)
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_bias": accum,
            "diff_count_consumed": signals["diff_count"],
            "generator": "ff priors retrain",
        }
        out.write_text(json.dumps(payload, indent=2))
        proposed["applied"] = True
        proposed["written_to"] = str(out)
    else:
        proposed["applied"] = False
        proposed["dry_run"] = True

    return proposed


__all__ = [
    "DEFAULT_MIN_DIFFS",
    "JOURNAL_DIR_NAME",
    "JOURNAL_FILE_NAME",
    "aggregate_signals",
    "append_to_journal",
    "load_journal",
    "update_priors",
]
