"""Tests for the heuristic shot proposer."""

from __future__ import annotations

from pathlib import Path

from fandomforge.intelligence.shot_proposer import (
    ProposerConfig, ProposerInputs, propose_shot_list,
)
from fandomforge.validation import validate


def _basic_inputs(**overrides):
    defaults = dict(
        project_slug="test-proj",
        edit_plan={
            "schema_version": 1,
            "project_slug": "test-proj",
            "concept": {"theme": "sacrifice", "one_sentence": "a test"},
            "song": {"title": "x", "artist": "y"},
            "fandoms": ["Marvel", "Star Wars"],
            "acts": [
                {"act": 1, "name": "setup", "end_sec": 30},
                {"act": 2, "name": "escalation", "end_sec": 60},
                {"act": 3, "name": "resolution", "end_sec": 90},
            ],
        },
        beat_map={
            "schema_version": 1,
            "duration_sec": 90.0,
            "bpm": 120.0,
            "downbeats": [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 30, 40, 50, 60, 70, 80],
            "drops": [
                {"time": 20.0, "intensity": 1.0, "type": "first_drop"},
                {"time": 60.0, "intensity": 1.0, "type": "main_drop"},
            ],
        },
        catalog=[],
        config=ProposerConfig(),
    )
    defaults.update(overrides)
    return ProposerInputs(**defaults)


def test_proposer_output_is_schema_valid():
    draft = propose_shot_list(_basic_inputs())
    validate(draft, "shot-list")  # raises if invalid


def test_proposer_is_deterministic():
    a = propose_shot_list(_basic_inputs())
    b = propose_shot_list(_basic_inputs())
    # generated_at is a wall-clock field — exclude it from the determinism check.
    assert a["shots"] == b["shots"]
    assert a["fps"] == b["fps"]
    assert a.get("fandom_quota") == b.get("fandom_quota")


def test_proposer_drops_become_hero_shots():
    draft = propose_shot_list(_basic_inputs())
    hero_shots = [s for s in draft["shots"] if s["role"] == "hero"]
    assert len(hero_shots) >= 1
    hero_times = {s["beat_sync"]["time_sec"] for s in hero_shots}
    assert 20.0 in hero_times or 60.0 in hero_times


def test_proposer_uses_catalog_clips_when_available():
    inputs = _basic_inputs(catalog=[
        {"id": "marvel_clip_1", "duration_sec": 60.0},
        {"id": "starwars_clip_1", "duration_sec": 45.0},
    ])
    draft = propose_shot_list(inputs)
    source_ids = {s["source_id"] for s in draft["shots"]}
    assert any(sid in {"marvel_clip_1", "starwars_clip_1"} for sid in source_ids)
    assert not any(sid.startswith("PLACEHOLDER_") for sid in source_ids)


def test_proposer_uses_placeholders_when_catalog_empty():
    draft = propose_shot_list(_basic_inputs())
    assert any(s["source_id"].startswith("PLACEHOLDER_") for s in draft["shots"])


def test_proposer_respects_act_boundaries():
    draft = propose_shot_list(_basic_inputs())
    for shot in draft["shots"]:
        time_sec = shot["beat_sync"]["time_sec"]
        if time_sec <= 30:
            assert shot["act"] == 1
        elif time_sec <= 60:
            assert shot["act"] == 2
        else:
            assert shot["act"] == 3


def test_proposer_sorts_shots_by_start_frame():
    draft = propose_shot_list(_basic_inputs())
    frames = [s["start_frame"] for s in draft["shots"]]
    assert frames == sorted(frames)


def test_proposer_no_overlapping_shots():
    draft = propose_shot_list(_basic_inputs())
    shots = draft["shots"]
    for i in range(len(shots) - 1):
        end_i = shots[i]["start_frame"] + shots[i]["duration_frames"]
        assert shots[i + 1]["start_frame"] >= end_i


def test_proposer_empty_beat_map_falls_back_to_grid():
    inputs = _basic_inputs(beat_map={"schema_version": 1, "duration_sec": 30.0})
    draft = propose_shot_list(inputs)
    assert len(draft["shots"]) > 0
    validate(draft, "shot-list")


# ---------- No-reuse dedupe ----------


def test_dedupe_no_duplicate_source_timecodes_with_enough_catalog():
    """With a reasonably-sized catalog, the proposer should never pick the
    same (source_id, offset) twice within 100ms."""
    inputs = _basic_inputs(catalog=[
        {"id": f"clip_{i}", "duration_sec": 120.0}
        for i in range(6)
    ])
    draft = propose_shot_list(inputs)
    keys = [
        (s["source_id"], round(_tc_to_sec(s["source_timecode"]), 1))
        for s in draft["shots"]
    ]
    assert len(set(keys)) == len(keys), (
        f"duplicate source+timecode in shot list: {keys}"
    )


def test_dedupe_tolerance_widens_with_small_catalog():
    """With only 1 clip and many shots, dedupe should emit a warning instead of crashing."""
    inputs = _basic_inputs(catalog=[
        {"id": "only_clip", "duration_sec": 10.0},  # very short — tight offset space
    ])
    draft = propose_shot_list(inputs)
    # Output still produced, just with warnings about widening
    assert draft["shots"], "proposer must still emit shots under pressure"
    assert "warnings" in draft, (
        "expected the proposer to record dedupe-tolerance-widened warnings"
    )
    assert any("reuse-dedupe" in w for w in draft["warnings"])


def test_dedupe_can_be_disabled():
    """With dedupe off, the same (source, offset) may appear multiple times."""
    cfg = ProposerConfig(dedupe=False)
    inputs = _basic_inputs(
        catalog=[{"id": "only_clip", "duration_sec": 10.0}],
        config=cfg,
    )
    draft = propose_shot_list(inputs)
    assert "warnings" not in draft or not any(
        "reuse-dedupe" in w for w in draft.get("warnings", [])
    )


# ---------- Intentional callback ----------


def test_callback_mirrors_earlier_shot():
    """If the edit plan declares a callback, the proposer reuses the earlier shot's
    source_id and timecode rather than picking a new one."""
    edit_plan = {
        "schema_version": 1,
        "project_slug": "test-proj",
        "concept": {"theme": "bookend", "one_sentence": "t"},
        "song": {"title": "x", "artist": "y"},
        "fandoms": ["Marvel"],
        "acts": [{"act": 1, "name": "a", "end_sec": 90}],
        "shot_intents": [
            {"id": "s003", "intent": "callback", "callback_of": "s001"},
        ],
    }
    inputs = _basic_inputs(
        edit_plan=edit_plan,
        catalog=[
            {"id": "clip_a", "duration_sec": 120.0},
            {"id": "clip_b", "duration_sec": 120.0},
        ],
    )
    draft = propose_shot_list(inputs)
    shots_by_id = {s["id"]: s for s in draft["shots"]}
    assert "s001" in shots_by_id and "s003" in shots_by_id
    s001 = shots_by_id["s001"]
    s003 = shots_by_id["s003"]
    assert s003["source_id"] == s001["source_id"]
    assert s003["source_timecode"] == s001["source_timecode"]
    assert s003.get("intent") == "callback"
    assert s003.get("callback_of") == "s001"


def _tc_to_sec(timecode: str) -> float:
    """Parse HH:MM:SS.mmm back to seconds for test assertions."""
    h, m, rest = timecode.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)
