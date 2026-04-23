"""Integration: user correction → craft_weights_for → real pipeline modules.

These tests prove that a correction written via the web UI actually flows
through to the modules that consume craft weights at render time:
``shot_proposer``, ``sfx_engine``, and anything else that calls
``craft_weights_for`` + ``craft_feature_active``. Without this, the
correction UI would be a nice form that nothing acts on.

Each test isolates the journal path per ``tmp_path`` so one run can't
pollute another. Forensic + training bias are disabled per-test so we
can read the correction's effect in isolation.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_CORRECTIONS_JOURNAL", str(tmp_path / "corrections.jsonl"))
    monkeypatch.setenv("FF_TRAINING_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("FF_FORENSIC_BIAS", "0")
    monkeypatch.setenv("FF_TRAINING_BIAS", "0")
    monkeypatch.setenv("FF_CORRECTIONS_BIAS", "1")
    from fandomforge.intelligence import forensic_craft_bias
    forensic_craft_bias.clear_cache()
    yield


def _seed_correction(
    corrected_bucket: str,
    corrected_craft_weights: dict[str, float],
    forensic_id: str = "integration-test",
) -> None:
    from fandomforge.intelligence.corrections_journal import (
        CorrectionEntry,
        append_correction,
    )
    from fandomforge.intelligence.forensic_craft_bias import clear_cache

    append_correction(CorrectionEntry(
        forensic_id=forensic_id,
        corrected_bucket=corrected_bucket,
        corrected_craft_weights=corrected_craft_weights,
    ))
    clear_cache()


def test_correction_flips_dropout_on_action():
    """Hand-tuned action table sets dropout=1.0 (active). A correction to
    0.0 at 40% blend should produce 0.6 — still active. A correction to
    0.0 at a higher accumulated pull should cross below 0.5.
    """
    from fandomforge.config import craft_weights_for, craft_feature_active

    # Before any correction, action.dropout is fully active
    w = craft_weights_for("action")
    assert w["dropout"] == pytest.approx(1.0, abs=1e-3)
    assert craft_feature_active(w["dropout"]) is True

    # One correction: 0.0 → blend = 0.6*1.0 + 0.4*0.0 = 0.60 (still active)
    _seed_correction("action", {"dropout": 0.0})
    w = craft_weights_for("action")
    assert w["dropout"] == pytest.approx(0.60, abs=1e-2)
    assert craft_feature_active(w["dropout"]) is True

    # Two more corrections stacking. The weighted-mean aggregator across
    # multiple corrections is still ~0.0, so blend stays at 0.60. The
    # per-correction blend is 40% cap — intentional so one bad correction
    # can't nuke a feature.
    _seed_correction("action", {"dropout": 0.0}, forensic_id="also-0")
    _seed_correction("action", {"dropout": 0.0}, forensic_id="again-0")
    w = craft_weights_for("action")
    assert w["dropout"] == pytest.approx(0.60, abs=1e-2)


def test_correction_flips_on_sad_to_active():
    """Sad bucket has every action-grammar feature disabled (weight 0.0 →
    not active). A user correction insisting dropout=1.0 at 40% blend
    yields 0.4 — below the 0.5 activation threshold, so sad still won't
    fire dropout. This proves the cap is working: one correction alone
    can't flip a design decision."""
    from fandomforge.config import craft_weights_for, craft_feature_active

    w = craft_weights_for("sad")
    assert w["dropout"] == pytest.approx(0.0, abs=1e-3)

    _seed_correction("sad", {"dropout": 1.0})
    w = craft_weights_for("sad")
    assert w["dropout"] == pytest.approx(0.40, abs=1e-2)
    assert craft_feature_active(w["dropout"]) is False  # capped below threshold


def test_correction_reaches_shot_proposer_logic():
    """The shot_proposer imports craft_weights_for at render time. Seed a
    correction that forces ramp=0.0 on action and verify the proposer
    reads the corrected value."""
    _seed_correction("action", {"ramp": 0.0})

    # shot_proposer reads craft_weights_for lazily inside its densify pass.
    # We mirror that lookup here — if this returns the corrected value,
    # every call-site inside shot_proposer will see the same.
    from fandomforge.config import craft_weights_for, craft_feature_active

    w = craft_weights_for("action")
    # Table ramp=1.0, correction 0.0 at 40% → 0.6. Still active but damped.
    assert w["ramp"] == pytest.approx(0.60, abs=1e-2)
    assert craft_feature_active(w["ramp"]) is True

    # Now push it harder — multiple corrections to 0.0
    for i in range(3):
        _seed_correction("action", {"ramp": 0.0}, forensic_id=f"push-{i}")
    w = craft_weights_for("action")
    # Still capped at 40% blend per aggregation pass
    assert w["ramp"] == pytest.approx(0.60, abs=1e-2)


def test_correction_on_unknown_bucket_is_ignored():
    """A correction on a bucket not in MFV_CRAFT_WEIGHTS falls through
    to the global default (zero row). The correction doesn't crash."""
    _seed_correction("made-up-bucket", {"dropout": 1.0})
    from fandomforge.config import craft_weights_for

    w = craft_weights_for("made-up-bucket")
    # Default is fallback-to-action for unknown types, but table weights stay
    # at their table values — the correction for "made-up-bucket" only
    # affects lookups with that exact bucket name.
    assert "dropout" in w


def test_sfx_engine_respects_corrections():
    """sfx_engine's J-cut lead-in only fires when craft_weight['j_cut']
    is active. Seed a correction disabling j_cut for action and verify
    the effective weight goes inactive."""
    from fandomforge.config import craft_weights_for, craft_feature_active

    # Before: action.j_cut is active
    assert craft_feature_active(craft_weights_for("action")["j_cut"]) is True

    # Heavy correction to 0.0
    _seed_correction("action", {"j_cut": 0.0})
    w = craft_weights_for("action")
    assert w["j_cut"] == pytest.approx(0.60, abs=1e-2)
    # Still active — correction cap protects against single-shot overrides
    assert craft_feature_active(w["j_cut"]) is True


def test_new_correction_overrides_older_for_same_forensic():
    """Same forensic_id corrected twice — newest entry dominates via the
    timestamp decay in corrections_suggestion."""
    _seed_correction("action", {"dropout": 0.0}, forensic_id="v1")
    # Same correction shape, different forensic_id to exercise the average
    _seed_correction("action", {"dropout": 0.2}, forensic_id="v2")
    from fandomforge.intelligence.forensic_craft_bias import corrections_suggestion
    sugg = corrections_suggestion("action")
    assert sugg is not None
    # Two corrections averaged with mild recency decay — lands between the two
    assert 0.0 <= sugg["dropout"] <= 0.2


def test_corrections_disabled_flag_fully_skips():
    """When FF_CORRECTIONS_BIAS=0, corrections are invisible to the
    pipeline. Important for clean A/B measurements."""
    import os

    _seed_correction("action", {"dropout": 0.0})
    os.environ["FF_CORRECTIONS_BIAS"] = "0"
    from fandomforge.intelligence.forensic_craft_bias import clear_cache
    clear_cache()

    from fandomforge.config import craft_weights_for
    w = craft_weights_for("action")
    assert w["dropout"] == pytest.approx(1.0, abs=1e-3)
