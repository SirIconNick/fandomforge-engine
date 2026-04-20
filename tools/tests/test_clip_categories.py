"""Tests for the clip-category taxonomy loader + schema."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.clip_categories import (
    CLIP_CATEGORY_IDS,
    categories,
    categories_for_zone,
    category,
    edit_type_bias,
    load_clip_categories,
)


class TestTaxonomyLoad:
    def test_canonical_data_loads_and_validates(self):
        data = load_clip_categories()
        assert data["schema_version"] == 1
        ids = [c["id"] for c in data["categories"]]
        assert set(ids) == set(CLIP_CATEGORY_IDS)

    def test_every_canonical_id_has_data(self):
        ids = {c["id"] for c in categories()}
        for cid in CLIP_CATEGORY_IDS:
            assert cid in ids, f"missing taxonomy entry for {cid}"


class TestLookups:
    def test_category_returns_record(self):
        c = category("establishing")
        assert c["id"] == "establishing"
        assert c["label"]
        assert c["description"]
        assert c["energy_zone_affinity"]

    def test_unknown_category_raises(self):
        with pytest.raises(KeyError):
            category("not-a-real-category")

    def test_edit_type_bias_default_is_one(self):
        # Pick a category that doesn't list every edit type
        c = category("establishing")
        # Inject a missing edit type — should fall back to 1.0
        bias = edit_type_bias("establishing", "totally-new-edit-type")
        assert bias == 1.0

    def test_edit_type_bias_returns_configured_value(self):
        # establishing has tribute=1.4 in canonical data
        bias = edit_type_bias("establishing", "tribute")
        assert bias == pytest.approx(1.4)


class TestZoneAffinity:
    def test_categories_for_low_includes_establishing_and_reactions(self):
        ids = categories_for_zone("low")
        assert "establishing" in ids
        assert "reaction-quiet" in ids
        # action-high is high/drop only, should NOT appear
        assert "action-high" not in ids

    def test_categories_for_drop_includes_climactic_and_action_high(self):
        ids = categories_for_zone("drop")
        assert "climactic" in ids
        assert "action-high" in ids

    def test_unknown_zone_returns_empty(self):
        assert categories_for_zone("not-a-real-zone") == []
