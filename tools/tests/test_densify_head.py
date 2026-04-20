"""Extra test for head-gap fill in densify_shot_list."""

from __future__ import annotations

from fandomforge.intelligence.shot_proposer import densify_shot_list


def test_fills_head_gap_from_zero_to_first_slot():
    """First slot shot starts at 10s. Densify must fill 0-10s so the timeline
    starts from the song's start — otherwise qa.duration complains about the
    missing head."""
    fps = 24.0
    sparse = {
        "schema_version": 1,
        "project_slug": "head-gap",
        "fps": fps,
        "resolution": {"width": 1920, "height": 1080},
        "song_duration_sec": 20.0,
        "shots": [
            {
                "id": "s001", "act": 1,
                "start_frame": int(10 * fps), "duration_frames": int(1 * fps),
                "source_id": "src-A", "source_timecode": "0:00:00.000",
                "role": "hero",
                "mood_tags": [], "framing": "", "motion_vector": None, "eyeline": "",
                "beat_sync": {"type": "drop", "index": 1, "time_sec": 10.0},
                "scores": {"theme_fit": 3.0, "fandom_balance": 3.0,
                           "emotion": 3.0, "beat_sync_score": 4.5},
            },
        ],
        "generated_at": "2026-04-21T00:00:00+00:00", "generator": "test",
    }
    out = densify_shot_list(sparse)
    head_fillers = [
        s for s in out["shots"]
        if s.get("densified") and s["start_frame"] < int(10 * fps)
    ]
    assert head_fillers, "expected head-gap fillers before t=10s"
    # First filler should start at frame 0
    assert head_fillers[0]["start_frame"] == 0
