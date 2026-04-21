"""Smoke tests for project-config loading + generalization invariants."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fandomforge.config import (
    ProjectConfig,
    load_project_config,
    save_project_config,
    build_era_patterns,
    character_aliases,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = REPO_ROOT / "projects"


# ---------------------------------------------------------------------------
# Defaults + dataclass behavior
# ---------------------------------------------------------------------------


def test_empty_config_loads_with_defaults(tmp_path: Path) -> None:
    proj = tmp_path / "blank"
    proj.mkdir()
    (proj / "project-config.yaml").write_text("character: test\n")
    cfg = load_project_config(proj)
    assert cfg.character == "test"
    assert cfg.template == "HauntedVeteran"
    assert cfg.run_qa is True
    assert cfg.song_gain_db == -8.0
    assert cfg.default_duck_db == -12.0
    assert cfg.export_presets == ["youtube"]


def test_unknown_keys_are_tolerated(tmp_path: Path) -> None:
    proj = tmp_path / "future"
    proj.mkdir()
    (proj / "project-config.yaml").write_text(
        "character: test\n"
        "experimental_future_field: 42\n"
        "another_unknown: [1, 2, 3]\n"
    )
    cfg = load_project_config(proj)
    assert cfg.character == "test"


def test_partial_config_doesnt_crash_on_missing_song(tmp_path: Path) -> None:
    proj = tmp_path / "nosong"
    proj.mkdir()
    (proj / "project-config.yaml").write_text("character: ghost\n")
    cfg = load_project_config(proj)
    assert cfg.song == ""
    assert cfg.song_path is None


def test_save_round_trip(tmp_path: Path) -> None:
    proj = tmp_path / "rt"
    proj.mkdir()
    cfg = ProjectConfig(
        character="rick",
        character_aliases=["rick grimes"],
        song="walking-dead-theme.mp3",
        template="HauntedVeteran",
        era_source_map={"S1": "rick-season-1", "S5": "rick-season-5"},
        narrative_priorities=["lori", "family", "coral"],
        song_gain_db=-6.0,
    )
    save_project_config(cfg, proj / "project-config.yaml")
    reloaded = load_project_config(proj)
    assert reloaded.character == "rick"
    assert reloaded.song == "walking-dead-theme.mp3"
    assert reloaded.era_source_map == {"S1": "rick-season-1", "S5": "rick-season-5"}
    assert reloaded.song_gain_db == -6.0


# ---------------------------------------------------------------------------
# Era patterns
# ---------------------------------------------------------------------------


def test_build_era_patterns_from_empty_config_falls_back_to_defaults() -> None:
    cfg = ProjectConfig()
    patterns = build_era_patterns(cfg)
    assert "RE2R-1998" in patterns
    assert "RE9-2026" in patterns


def test_build_era_patterns_from_custom_map() -> None:
    cfg = ProjectConfig(
        era_source_map={
            "BestScenes": "dean-winchester-best-scenes",
            "LaterSeasons": "dean-later-seasons",
        },
    )
    patterns = build_era_patterns(cfg)
    assert "BestScenes" in patterns
    assert "LaterSeasons" in patterns
    # Confirm regex tolerates underscore/dash/space variants
    pat = patterns["BestScenes"]
    import re
    assert re.search(pat, "dean-winchester-best-scenes")
    assert re.search(pat, "dean_winchester_best_scenes")


def test_character_aliases_dedups_case_insensitive() -> None:
    cfg = ProjectConfig(
        character="Dean",
        character_aliases=["dean", "DEAN", "Dean Winchester", "dean winchester"],
    )
    aliases = character_aliases(cfg)
    assert aliases == ["dean", "dean winchester"]


# ---------------------------------------------------------------------------
# Real project configs under projects/ should load
# ---------------------------------------------------------------------------


def _real_project_slugs() -> list[str]:
    """Enumerate real projects, or return empty list if projects/ is absent.

    This repo ships without projects/ (content lives elsewhere per NOTES.md).
    Avoid touching the filesystem at collection time so pytest doesn't crash
    when the directory is missing.
    """
    if not PROJECTS_DIR.exists():
        return []
    return [p.name for p in PROJECTS_DIR.iterdir() if (p / "project-config.yaml").exists()]


@pytest.mark.parametrize("slug", _real_project_slugs())
def test_every_real_project_config_loads(slug: str) -> None:
    cfg = load_project_config(PROJECTS_DIR / slug)
    assert cfg.character, f"{slug} has no character set"
    assert cfg.template, f"{slug} has no template set"
    # Every real project should have song mapped
    assert cfg.song, f"{slug} has no song"


@pytest.mark.skipif(
    not (Path(__file__).parent.parent.parent / "projects" / "leon-badass-monologue").exists(),
    reason="leon-badass-monologue project not present in this copy",
)
def test_leon_config_unchanged_fields() -> None:
    """Regression guard — if this fails, Leon config was edited; verify intent."""
    cfg = load_project_config(PROJECTS_DIR / "leon-badass-monologue")
    assert cfg.character == "leon"
    assert "leon kennedy" in [a.lower() for a in cfg.character_aliases]
    assert cfg.template == "HauntedVeteran"
    # Era map must contain the primary RE tags
    assert "RE9" in cfg.era_source_map
    assert "RE2R" in cfg.era_source_map


@pytest.mark.skipif(
    not (
        (Path(__file__).parent.parent.parent / "projects" / "dean-winchester-renegades").exists()
        and (Path(__file__).parent.parent.parent / "projects" / "leon-badass-monologue").exists()
    ),
    reason="Dean/Leon reference projects not present in this copy",
)
def test_dean_config_is_fully_different_from_leon() -> None:
    """Generalization proof — Dean and Leon share zero character-identifying fields."""
    dean = load_project_config(PROJECTS_DIR / "dean-winchester-renegades")
    leon = load_project_config(PROJECTS_DIR / "leon-badass-monologue")
    assert dean.character != leon.character
    assert dean.song != leon.song
    assert dean.vision_context != leon.vision_context
    assert set(dean.era_source_map) != set(leon.era_source_map)
    # Dean needs hotter-song tuning
    assert dean.song_gain_db <= leon.song_gain_db
