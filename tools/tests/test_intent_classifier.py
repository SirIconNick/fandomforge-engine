"""Tests for the intent classifier (Phase 0.5.5)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.intent_classifier import (
    TONE_DIMS,
    classify_intent,
)
from fandomforge.validation import validate


class TestExplicitVsClassified:
    def test_explicit_edit_type_in_config_wins(self):
        intent = classify_intent(
            "epic action montage with fights",
            project_config={"edit_type": "tribute"},
        )
        assert intent["edit_type"] == "tribute"
        assert intent["edit_type_source"] == "explicit"

    def test_classifier_picks_when_no_explicit(self):
        intent = classify_intent("emotional tribute to a fallen hero")
        assert intent["edit_type"] in {"tribute", "emotional", "sad_emotional"}
        assert intent["edit_type_source"] == "classified"

    def test_default_when_text_empty_and_no_config(self):
        intent = classify_intent("")
        assert intent["edit_type"] == "action"
        assert intent["edit_type_source"] == "default"


class TestExtendedTypes:
    def test_dialogue_narrative_recognized(self):
        intent = classify_intent(
            "build a monologue from stitched dialogue snippets across films"
        )
        assert intent["edit_type"] == "dialogue_narrative"

    def test_dance_movement_recognized(self):
        intent = classify_intent("a K-pop fancam-style dance edit")
        assert intent["edit_type"] == "dance_movement"

    def test_sad_emotional_recognized(self):
        intent = classify_intent("a heartbreak elegy in memory of a lost hero")
        assert intent["edit_type"] == "sad_emotional"


class TestToneVector:
    def test_grief_keywords_load_grief_dim(self):
        intent = classify_intent("grief and mourning across the funeral scenes")
        assert intent["tone_vector"][TONE_DIMS.index("grief")] > 0.5

    def test_triumph_keywords_load_triumph_dim(self):
        intent = classify_intent("victory rebirth comeback champion rises")
        idx = TONE_DIMS.index("triumph")
        assert intent["tone_vector"][idx] > 0.5

    def test_empty_prompt_gives_zero_tone(self):
        intent = classify_intent("")
        assert intent["tone_vector"] == [0.0] * 8


class TestDurationParsing:
    def test_parses_seconds_from_prompt(self):
        intent = classify_intent("make a 30 second action edit")
        assert intent["target_duration_sec"] == 30.0
        assert intent["duration_source"] == "prompt"

    def test_parses_minutes_from_prompt(self):
        intent = classify_intent("a 2 minute tribute")
        assert intent["target_duration_sec"] == 120.0
        assert intent["duration_source"] == "prompt"

    def test_full_song_uses_song_duration(self):
        intent = classify_intent("full song hype edit", song_duration_sec=180.0)
        assert intent["target_duration_sec"] == 180.0
        assert intent["duration_source"] == "prompt"

    def test_falls_back_to_song_duration(self):
        intent = classify_intent("just an action edit", song_duration_sec=210.0)
        assert intent["target_duration_sec"] == 210.0
        assert intent["duration_source"] == "song"

    def test_default_when_nothing_known(self):
        intent = classify_intent("just an action edit")
        assert intent["duration_source"] == "default"


class TestSpeakers:
    def test_extracts_capitalized_names(self):
        intent = classify_intent("a tribute to Leon Kennedy across the RE arc")
        names = [s["name"] for s in intent["speakers"]]
        assert "Leon Kennedy" in names

    def test_marks_fandom_roster_matches(self):
        intent = classify_intent(
            "tribute to John Wick",
            fandom_roster=[{"name": "John Wick"}],
        )
        speakers = intent["speakers"]
        match = next((s for s in speakers if s["name"] == "John Wick"), None)
        assert match is not None
        assert match["evidence"] == "fandom_roster_match"
        assert match["fandom"] == "John Wick"


class TestConfidence:
    def test_explicit_type_boosts_confidence(self):
        a = classify_intent("epic action", project_config={"edit_type": "action"})
        b = classify_intent("epic action")
        assert a["confidence"] > b["confidence"]

    def test_low_confidence_flags_user_confirmation(self):
        intent = classify_intent("")
        assert intent["needs_user_confirmation"] is True

    def test_high_confidence_does_not_flag(self):
        intent = classify_intent(
            "tribute to Leon Kennedy",
            project_config={"edit_type": "tribute"},
            song_duration_sec=180.0,
            fandom_roster=[{"name": "Leon Kennedy"}],
        )
        assert intent["needs_user_confirmation"] is False


class TestSchemaCompliance:
    def test_output_validates(self):
        intent = classify_intent(
            "tribute to Leon Kennedy",
            project_config={"edit_type": "tribute"},
            song_duration_sec=180.0,
        )
        validate(intent, "intent")
