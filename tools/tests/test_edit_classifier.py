"""Edit-type classifier tests."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.edit_classifier import (
    DEFAULT_TYPE,
    available_types,
    classify_edit_type,
    load_type_priors,
    resolve_edit_type,
)


class TestAvailableTypes:
    def test_eight_types(self) -> None:
        types = available_types()
        assert "action" in types
        assert "emotional" in types
        assert "tribute" in types
        assert "shipping" in types
        assert "speed_amv" in types
        assert "cinematic" in types
        assert "comedy" in types
        assert "hype_trailer" in types


class TestLoadTypePriors:
    def test_action_has_fast_pacing(self) -> None:
        p = load_type_priors("action")
        assert p is not None
        assert p["target_shot_duration_sec"] == 1.0
        assert p["target_cuts_per_minute"] == 50

    def test_emotional_has_slow_pacing(self) -> None:
        p = load_type_priors("emotional")
        assert p is not None
        assert p["target_shot_duration_sec"] == 4.0
        assert p["target_cuts_per_minute"] == 15

    def test_unknown_returns_none(self) -> None:
        assert load_type_priors("unknown-type") is None


class TestClassifyEditType:
    def test_empty_returns_default(self) -> None:
        assert classify_edit_type("") == DEFAULT_TYPE
        assert classify_edit_type(None) == DEFAULT_TYPE

    def test_strong_tribute_signal(self) -> None:
        assert classify_edit_type("Tony Stark tribute across the MCU") == "tribute"

    def test_strong_shipping_signal(self) -> None:
        assert classify_edit_type("Kirk x Spock otp edit") == "shipping"

    def test_strong_amv_signal(self) -> None:
        assert classify_edit_type("My Hero Academia AMV speed edit") == "speed_amv"

    def test_strong_trailer_signal(self) -> None:
        assert classify_edit_type("Fan trailer for the next chapter") == "hype_trailer"

    def test_strong_memorial_signal(self) -> None:
        assert classify_edit_type("In memoriam — final scenes") == "tribute"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("punches and explosions across Marvel and DC", "action"),
            ("grief over losing the mentor", "emotional"),
            ("falling in love slowly over many episodes", "shipping"),
            ("jokes and ridiculous crack moments", "comedy"),
            ("atmospheric arthouse story-driven narrative piece", "cinematic"),
            ("rising stakes origin story reveal", "hype_trailer"),
        ],
    )
    def test_weak_signal_classification(self, text: str, expected: str) -> None:
        assert classify_edit_type(text) == expected

    def test_no_match_falls_back_to_default(self) -> None:
        # No keyword matches anywhere
        assert classify_edit_type("xyzzy nothingburger lorem") == DEFAULT_TYPE


class TestResolveEditType:
    def test_explicit_config_wins(self) -> None:
        t, source = resolve_edit_type(
            {"edit_type": "cinematic"},
            {"concept": {"theme": "action hero punches and explosions"}},
        )
        assert t == "cinematic"
        assert source == "config"

    def test_classified_from_edit_plan_concept(self) -> None:
        t, source = resolve_edit_type(
            None,
            {"concept": {"theme": "grief and loss after the war"}},
        )
        assert t == "emotional"
        assert source == "classified"

    def test_default_when_nothing_available(self) -> None:
        t, source = resolve_edit_type(None, None)
        assert t == DEFAULT_TYPE
        assert source == "default"

    def test_invalid_config_type_falls_through(self) -> None:
        t, source = resolve_edit_type(
            {"edit_type": "unknown-garbage"},
            {"concept": {"theme": "action fight"}},
        )
        # Invalid config type ignored, classifier picks from theme
        assert t == "action"
        assert source == "classified"
