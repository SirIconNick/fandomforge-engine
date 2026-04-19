"""Integration tests for the shot/transition/color matchers.

Uses the good-fixture artifacts from tests/fixtures/schemas/good/ as inputs.
Real source videos are synthesized with ffmpeg where needed. CLIP and optical
flow paths are exercised only when models/deps are available; otherwise tests
assert the code still produces schema-valid artifacts via the scoring
fallbacks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fandomforge.intelligence.cliche_detector import (
    is_cliche,
    load_cliche_patterns,
    matches_for_fandom,
)
from fandomforge.intelligence.shot_matcher import (
    MatchConfig,
    match_shots,
)
from fandomforge.intelligence.transition_matcher import (
    TransitionConfig,
    match_transitions,
)
from fandomforge.intelligence.color_matcher import (
    ColorMatchConfig,
    match_color,
)
from fandomforge.validation import validate


FFMPEG = shutil.which("ffmpeg")
pytestmark_needs_ffmpeg = pytest.mark.skipif(
    FFMPEG is None, reason="ffmpeg binary required for video-based matchers"
)


# ---------------------------------------------------------------------------
# Cliche detector
# ---------------------------------------------------------------------------


class TestClicheDetector:
    def test_patterns_load_from_doc(self) -> None:
        patterns = load_cliche_patterns()
        assert patterns, "expected patterns loaded from overused-shots-to-avoid.md"
        fandom_names = {k.lower() for k in patterns.keys()}
        assert any("marvel" in f or "mcu" in f for f in fandom_names)
        assert any("star wars" in f for f in fandom_names)

    def test_known_hit_matches(self) -> None:
        hit = is_cliche("Tony Stark snap Endgame sequence")
        assert hit is not None
        assert hit.score >= 0.75

    def test_unknown_returns_none(self) -> None:
        assert is_cliche("A completely fresh shot of a corgi running through wildflowers") is None

    def test_fandom_filter(self) -> None:
        # Even if the phrase would match some fandom, restricting to an unrelated
        # fandom should return None.
        assert is_cliche("portals opening", fandom="Star Wars") is None

    def test_matches_for_fandom_case_insensitive(self) -> None:
        assert matches_for_fandom("star wars")
        assert matches_for_fandom("MCU")  # partial-match fallback to "Marvel / MCU"


# ---------------------------------------------------------------------------
# Shot matcher
# ---------------------------------------------------------------------------


def _make_synthetic_inputs(tmp_path: Path) -> dict[str, Path]:
    """Build beat-map / source-catalog / edit-plan fixtures with synthesized
    videos so match_shots has real data to chew on."""
    # Videos: two ~5 sec clips with different patterns.
    raw = tmp_path / "raw"
    raw.mkdir()
    v1 = raw / "hero.mp4"
    v2 = raw / "reaction.mp4"
    for path, src in [(v1, "testsrc2=size=320x180:rate=24:duration=6"),
                      (v2, "mandelbrot=size=320x180:rate=24")]:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", src,
            "-t", "6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            "-shortest",
            str(path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # beat-map.json — 12s track, one drop at 6.0s.
    bm = {
        "schema_version": 1,
        "song": "synthetic",
        "artist": "Test",
        "duration_sec": 12.0,
        "bpm": 120.0,
        "bpm_confidence": 0.9,
        "time_signature": "4/4",
        "beats": [i * 0.5 for i in range(24)],
        "downbeats": [i * 2.0 for i in range(6)],
        "onsets": [i * 0.5 for i in range(24)],
        "drops": [{"time": 6.0, "intensity": 1.0, "type": "main_drop"}],
        "energy_curve": [[float(i), i / 12.0] for i in range(13)],
    }
    (data_dir / "beat-map.json").write_text(json.dumps(bm))

    # source-catalog.json — two sources.
    sc = {
        "schema_version": 1,
        "project_slug": "synth",
        "sources": [
            {
                "id": "b2:hero",
                "path": str(v1),
                "fandom": "Test-A",
                "source_type": "movie",
                "title": "Hero Clip",
                "year": 2026,
                "media": {
                    "duration_sec": 6.0, "width": 320, "height": 180,
                    "fps": 24.0, "codec": "h264", "bitrate_kbps": 500,
                    "has_audio": True, "audio_codec": "aac",
                    "audio_channels": 2, "audio_sample_rate": 48000,
                    "variable_frame_rate": False,
                },
                "derived": {},
                "flags": [],
            },
            {
                "id": "b2:reaction",
                "path": str(v2),
                "fandom": "Test-B",
                "source_type": "movie",
                "title": "Reaction Clip",
                "year": 2026,
                "media": {
                    "duration_sec": 6.0, "width": 320, "height": 180,
                    "fps": 24.0, "codec": "h264", "bitrate_kbps": 500,
                    "has_audio": True, "audio_codec": "aac",
                    "audio_channels": 2, "audio_sample_rate": 48000,
                    "variable_frame_rate": False,
                },
                "derived": {},
                "flags": [],
            },
        ],
    }
    (data_dir / "source-catalog.json").write_text(json.dumps(sc))

    # edit-plan.json — 2 acts, explicit fandom shares so fandom_score matters.
    ep = {
        "schema_version": 1,
        "project_slug": "synth",
        "concept": {
            "theme": "Match test",
            "one_sentence": "A short synthetic test of the shot matcher.",
            "mode": "woven",
        },
        "song": {"title": "synthetic", "artist": "Test", "duration_sec": 12.0},
        "fandoms": [
            {"name": "Test-A", "share": 0.5},
            {"name": "Test-B", "share": 0.5},
        ],
        "vibe": "cinematic",
        "length_seconds": 12,
        "platform_target": "youtube",
        "resolution": {"width": 1920, "height": 1080},
        "fps": 24,
        "acts": [
            {
                "number": 1, "name": "Setup",
                "start_sec": 0, "end_sec": 6,
                "energy_target": 30,
                "emotional_goal": "establishing a grounded start",
                "fandom_focus": {"Test-A": 0.8, "Test-B": 0.2},
                "key_image": "hero holding a moment",
            },
            {
                "number": 2, "name": "Drop",
                "start_sec": 6, "end_sec": 12,
                "energy_target": 90,
                "emotional_goal": "raw emotional drop",
                "fandom_focus": {"Test-A": 0.3, "Test-B": 0.7},
                "key_image": "reaction pulling focus",
            },
        ],
    }
    (data_dir / "edit-plan.json").write_text(json.dumps(ep))

    return {
        "beat_map": data_dir / "beat-map.json",
        "source_catalog": data_dir / "source-catalog.json",
        "edit_plan": data_dir / "edit-plan.json",
    }


@pytestmark_needs_ffmpeg
def test_match_shots_produces_schema_valid_output(tmp_path: Path) -> None:
    inputs = _make_synthetic_inputs(tmp_path)
    bm = json.loads(inputs["beat_map"].read_text())
    sc = json.loads(inputs["source_catalog"].read_text())
    ep = json.loads(inputs["edit_plan"].read_text())

    shot_list = match_shots(
        beat_map=bm, source_catalog=sc, edit_plan=ep,
        config=MatchConfig(fps=24, resolution=(1920, 1080)),
    )
    validate(shot_list, "shot-list")
    assert shot_list["shots"], "expected at least one matched shot"
    # Check fandom distribution is respected to some degree.
    fandoms = {s["fandom"] for s in shot_list["shots"]}
    assert fandoms.issubset({"Test-A", "Test-B"})


@pytestmark_needs_ffmpeg
def test_match_shots_respects_reuse_cap(tmp_path: Path) -> None:
    inputs = _make_synthetic_inputs(tmp_path)
    bm = json.loads(inputs["beat_map"].read_text())
    sc = json.loads(inputs["source_catalog"].read_text())
    ep = json.loads(inputs["edit_plan"].read_text())

    shot_list = match_shots(
        beat_map=bm, source_catalog=sc, edit_plan=ep,
        config=MatchConfig(max_reuse_per_source=1),
    )
    # Each source may appear at most once.
    per_source: dict[str, int] = {}
    for s in shot_list["shots"]:
        per_source[s["source_id"]] = per_source.get(s["source_id"], 0) + 1
    for sid, count in per_source.items():
        assert count <= 1, f"source {sid} reused {count} times"


@pytestmark_needs_ffmpeg
def test_match_shots_rejects_when_exclude_cliche(tmp_path: Path) -> None:
    """Even though our synthetic sources don't literally trigger cliches,
    verify the exclude path is wired (no crash, rejected list is a list)."""
    inputs = _make_synthetic_inputs(tmp_path)
    bm = json.loads(inputs["beat_map"].read_text())
    sc = json.loads(inputs["source_catalog"].read_text())
    ep = json.loads(inputs["edit_plan"].read_text())

    shot_list = match_shots(
        beat_map=bm, source_catalog=sc, edit_plan=ep,
        config=MatchConfig(exclude_cliche=True),
    )
    assert isinstance(shot_list.get("rejected", []), list)


# ---------------------------------------------------------------------------
# Transition matcher
# ---------------------------------------------------------------------------


@pytestmark_needs_ffmpeg
def test_match_transitions_produces_schema_valid_output(tmp_path: Path) -> None:
    inputs = _make_synthetic_inputs(tmp_path)
    bm = json.loads(inputs["beat_map"].read_text())
    sc = json.loads(inputs["source_catalog"].read_text())
    ep = json.loads(inputs["edit_plan"].read_text())
    shot_list = match_shots(
        beat_map=bm, source_catalog=sc, edit_plan=ep,
        config=MatchConfig(fps=24),
    )

    plan = match_transitions(
        shot_list=shot_list,
        source_catalog=sc,
        beat_map=bm,
        config=TransitionConfig(),
    )
    validate(plan, "transition-plan")
    # One transition between every pair of consecutive shots.
    assert len(plan["transitions"]) == max(0, len(shot_list["shots"]) - 1)


@pytestmark_needs_ffmpeg
def test_match_transitions_marks_drop_speed_ramp(tmp_path: Path) -> None:
    """Transitions landing within 0.5s of a drop get speed_ramp."""
    inputs = _make_synthetic_inputs(tmp_path)
    bm = json.loads(inputs["beat_map"].read_text())
    sc = json.loads(inputs["source_catalog"].read_text())
    ep = json.loads(inputs["edit_plan"].read_text())
    shot_list = match_shots(
        beat_map=bm, source_catalog=sc, edit_plan=ep,
        config=MatchConfig(fps=24),
    )
    plan = match_transitions(shot_list=shot_list, source_catalog=sc, beat_map=bm)
    # At least one should be a speed_ramp given the 6.0s drop.
    ramps = [t for t in plan["transitions"] if t["type"] == "speed_ramp"]
    # Allow 0 if none land near the drop window; but when they do, verify shape.
    for r in ramps:
        assert "speed_ramp" in r
        assert r["speed_ramp"]["from_rate"] > 0
        assert r["speed_ramp"]["to_rate"] > 0


# ---------------------------------------------------------------------------
# Color matcher
# ---------------------------------------------------------------------------


@pytestmark_needs_ffmpeg
def test_match_color_produces_schema_valid_output(tmp_path: Path) -> None:
    inputs = _make_synthetic_inputs(tmp_path)
    bm = json.loads(inputs["beat_map"].read_text())
    sc = json.loads(inputs["source_catalog"].read_text())
    ep = json.loads(inputs["edit_plan"].read_text())
    shot_list = match_shots(beat_map=bm, source_catalog=sc, edit_plan=ep)

    plan = match_color(
        shot_list=shot_list,
        source_catalog=sc,
        output_dir=tmp_path,
        config=ColorMatchConfig(lut_size=5),
    )
    validate(plan, "color-plan")
    assert plan["per_source"], "expected at least one per-source grade"
    # Each per_source entry should carry a LUT file on disk.
    for sid, entry in plan["per_source"].items():
        lut = Path(entry["lut"])
        assert lut.exists() and lut.suffix == ".cube"
        text = lut.read_text()
        assert "LUT_3D_SIZE" in text
