"""Tests for the Phase 6 dialogue-narrative pipeline (script → search →
lipsync → place)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.dialogue_lipsync import (
    PLAUSIBILITY_FLOOR, score_candidate,
)
from fandomforge.intelligence.dialogue_place import (
    assign_lines_to_windows, build_mixer_cues,
)
from fandomforge.intelligence.dialogue_script import build_script
from fandomforge.intelligence.dialogue_search import (
    search_for_line, search_script,
)
from fandomforge.validation import validate


# ---------- 6.1 dialogue_script ----------

class TestBuildScript:
    def test_speaker_pattern_extracted(self):
        prompt = """
        Leon: I'm not who I was.
        Ada: Then who are you now?
        Leon: We'll see.
        """
        script = build_script(prompt, project_slug="demo")
        validate(script, "dialogue-script")
        texts = [l["text"] for l in script["lines"]]
        assert "I'm not who I was." in texts
        assert "Then who are you now?" in texts

    def test_quoted_strings_extracted(self):
        prompt = 'The line is "you fought your way back" and we go.'
        script = build_script(prompt, project_slug="demo")
        assert any("you fought your way back" in l["text"] for l in script["lines"])

    def test_intent_classified_question(self):
        script = build_script("Why did you come back?", project_slug="x")
        assert script["lines"][0]["intent"] == "question"

    def test_intent_classified_defiant(self):
        script = build_script("I'm not your enemy.", project_slug="x")
        assert script["lines"][0]["intent"] == "defiant"

    def test_max_lines_capped(self):
        prompt = "\n".join(f"Speaker: line {i}." for i in range(20))
        script = build_script(prompt, project_slug="x", max_lines=5)
        assert len(script["lines"]) <= 5

    def test_empty_prompt_yields_empty_script(self):
        script = build_script("", project_slug="x")
        validate(script, "dialogue-script")
        assert script["lines"] == []


# ---------- 6.2 dialogue_search ----------

class TestSearchForLine:
    def _transcript(self, words: list[tuple[float, float, str]]) -> dict:
        return {
            "language": "en",
            "model": "test",
            "words": [
                {"start_sec": s, "end_sec": e, "text": t, "confidence": 0.9}
                for s, e, t in words
            ],
        }

    def test_exact_match_scores_high(self):
        transcripts = {
            "src": self._transcript([
                (0.0, 0.5, "you"), (0.5, 1.0, "fought"),
                (1.0, 1.5, "your"), (1.5, 2.0, "way"),
                (2.0, 2.5, "back"),
            ]),
        }
        line = {"index": 0, "text": "you fought your way back",
                "target_duration_ms": 2500, "speaker_role": "any"}
        results = search_for_line(line, transcripts, top_k=3)
        assert results
        assert results[0].composite_score > 0.5

    def test_no_match_returns_empty_or_low(self):
        transcripts = {
            "src": self._transcript([(0.0, 0.5, "completely"), (0.5, 1.0, "different")]),
        }
        line = {"index": 0, "text": "you fought your way back",
                "target_duration_ms": 2500}
        results = search_for_line(line, transcripts)
        # All scores should be below the high-quality threshold
        assert all(r.composite_score < 0.5 for r in results)

    def test_search_script_processes_all_lines(self):
        transcripts = {
            "src": self._transcript([
                (0.0, 0.5, "hello"), (0.5, 1.0, "world"),
                (5.0, 5.5, "goodbye"),
            ]),
        }
        script = {
            "lines": [
                {"index": 0, "text": "hello world"},
                {"index": 1, "text": "goodbye"},
            ],
        }
        results = search_script(script, transcripts, top_k=2)
        assert "0" in results and "1" in results


# ---------- 6.3 dialogue_lipsync ----------

class TestLipsyncScorer:
    def test_high_word_density_passes(self):
        transcript = {
            "words": [
                {"start_sec": s, "end_sec": s + 0.3, "text": "word", "confidence": 0.9}
                for s in [0.0, 0.4, 0.8, 1.2, 1.6]
            ],
        }
        cand = {"line_index": 0, "candidate_index": 0,
                "source_id": "src", "start_sec": 0.0, "end_sec": 2.0}
        scene = {"scenes": [{"start_sec": 0, "end_sec": 5,
                              "motion": 0.3, "visual_quality": 75}]}
        res = score_candidate(cand, transcript=transcript, scene_data=scene)
        assert res.accepted
        assert res.plausibility >= PLAUSIBILITY_FLOOR

    def test_no_words_low_plausibility(self):
        cand = {"line_index": 0, "candidate_index": 0,
                "source_id": "src", "start_sec": 0.0, "end_sec": 2.0}
        res = score_candidate(cand, transcript={"words": []}, scene_data=None)
        assert res.plausibility < PLAUSIBILITY_FLOOR
        assert any("word density" in r for r in res.reasons)

    def test_chaotic_motion_penalized(self):
        transcript = {
            "words": [
                {"start_sec": s, "end_sec": s + 0.3, "text": "x", "confidence": 0.9}
                for s in [0.0, 0.4, 0.8]
            ],
        }
        cand = {"line_index": 0, "candidate_index": 0,
                "source_id": "src", "start_sec": 0.0, "end_sec": 2.0}
        scene = {"scenes": [{"start_sec": 0, "end_sec": 5,
                              "motion": 0.85, "visual_quality": 60}]}
        res = score_candidate(cand, transcript=transcript, scene_data=scene)
        # Penalty is applied even with words present
        assert res.static_shot_penalty > 0


# ---------- 6.4 dialogue_place ----------

class TestPlacement:
    def _windows(self, n_safe: int = 3) -> dict:
        windows = []
        for i in range(n_safe):
            t = i * 5.0
            windows.append({
                "start_sec": t, "end_sec": t + 4.0,
                "flag": "SAFE", "reason_codes": ["low_energy"],
                "min_duration_available_sec": 4.0,
                "rms_at_start": 0.1, "mid_density_at_start": 0.05,
            })
        return {"schema_version": 1, "duration_sec": 60, "resolution_sec": 0.25,
                "safe_window_count": n_safe, "risky_window_count": 0,
                "blocked_window_count": 0, "windows": windows}

    def test_one_line_placed_in_first_safe_window(self):
        script = {"lines": [{"index": 0, "text": "hello",
                              "target_duration_ms": 1000}]}
        candidates = {
            "0": [{"line_index": 0, "source_id": "src", "start_sec": 0.0,
                   "end_sec": 1.0, "transcript_text": "hello",
                   "composite_score": 0.9}],
        }
        placements = assign_lines_to_windows(script, candidates, self._windows(3))
        assert len(placements) == 1
        assert placements[0].decision == "PLACE"
        assert placements[0].placed_song_time_sec == 0.0

    def test_overflow_lines_rejected(self):
        # 4 lines but only 2 SAFE windows
        script = {"lines": [
            {"index": i, "text": f"line {i}", "target_duration_ms": 3500}
            for i in range(4)
        ]}
        candidates = {
            str(i): [{"line_index": i, "source_id": "src", "start_sec": 0.0,
                      "end_sec": 3.5, "transcript_text": f"line {i}",
                      "composite_score": 0.8}]
            for i in range(4)
        }
        placements = assign_lines_to_windows(script, candidates, self._windows(2))
        placed = [p for p in placements if p.decision == "PLACE"]
        rejected = [p for p in placements if p.decision == "REJECT"]
        assert len(placed) <= 2
        assert len(rejected) >= 2

    def test_no_candidate_rejects_line(self):
        script = {"lines": [{"index": 0, "text": "no match",
                              "target_duration_ms": 1500}]}
        placements = assign_lines_to_windows(script, {}, self._windows(3))
        assert placements[0].decision == "REJECT"
        assert "no candidate" in placements[0].reason

    def test_build_mixer_cues_skips_rejects(self):
        placements = [
            type("P", (), {
                "decision": "PLACE", "chosen_candidate": {
                    "source_id": "src", "start_sec": 0.0, "end_sec": 1.5,
                    "transcript_text": "hello",
                },
                "placed_song_time_sec": 5.0, "line_text": "hello",
                "line_index": 0,
            })(),
            type("P", (), {
                "decision": "REJECT", "chosen_candidate": None,
                "placed_song_time_sec": 0.0, "line_text": "x", "line_index": 1,
            })(),
        ]
        cues = build_mixer_cues(placements)
        assert len(cues) == 1
        assert cues[0]["start"] == 5.0
        assert cues[0]["line"] == "hello"
