"""ProjectConfig — single source of truth for a tribute video project.

Each project lives under `projects/<slug>/` and has a `project-config.yaml`
(or `.json`) that tells the engine everything character-, era-, and
media-specific about the project. No hardcoded character or era assumptions
in any engine module.

Example `projects/leon-badass-monologue/project-config.yaml`:

    character: leon
    character_aliases: [leon kennedy, kennedy]
    era_source_map:
      RE2R: leon-re2r-cutscenes
      RE4R: leon-re4r-cutscenes
      RE6: leon-re6-cutscenes
      Damnation: leon-damnation
      ID: leon-infinite-darkness
      Vendetta: leon-vendetta
      RE9: re9-leon-scenepack
    narrative_priorities:
      - leon kennedy
      - dso
      - raccoon city
      - couldn't save
      - it's over victor
    song: in-the-end-tommee.mp3
    song_offset_sec: 0.0
    template: HauntedVeteran
    vision_context: "Resident Evil 9 game cutscene"

All fields have sensible defaults. Missing files still load — engine falls back
to generic character-agnostic behavior. But projects that want custom era
mapping, vision prompts, or narrative priorities SHOULD provide them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import yaml  # type: ignore
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ProjectConfig:
    """Everything a project needs to drive the engine.

    All fields default to sensible values so partial configs still load.
    """

    # --- Identity ---
    character: str = "unknown"
    character_aliases: list[str] = field(default_factory=list)

    # --- Source video metadata ---
    # Maps era/time-period key (e.g. "RE2R") to the stem of the source mp4 in
    # projects/<slug>/raw/. Used by:
    #   - layered_planner for parsing WAV filenames to source mp4
    #   - shot_library.detect_era for era column assignment
    #   - color_grader for per-era color recipes (future)
    era_source_map: dict[str, str] = field(default_factory=dict)

    # Blacklist source stems that shouldn't feed into the edit — typically
    # gameplay compilations with HUD overlays or watermarked third-party
    # montages (FILMISNOW, GMDEPTV). Shots from these sources get excluded
    # from the broll candidate pool. List stems without extension.
    excluded_sources: list[str] = field(default_factory=list)

    # --- Narrative priorities ---
    # Keywords/phrases to boost when selecting dialogue lines + scenes.
    # Character-specific — e.g. ["dso", "raccoon city", "couldn't save"]
    # for Leon.
    narrative_priorities: list[str] = field(default_factory=list)

    # --- Era arc ---
    # Per-time-range source allowlist for B-roll. Drives flashback
    # structure. Each entry: {start: float, end: float, sources: [stems]}.
    # During [start, end) the planner restricts B-roll to those sources
    # only. Empty list = no era filtering (B-roll picks from all eras).
    era_arc: list[dict] = field(default_factory=list)

    # --- Concept beats ---
    # Force-inserted shots at specific render timestamps to make the VO
    # line up with literal on-screen content (e.g. "I couldn't save them"
    # → Kendo gunshop scene). Each entry: {time: float, duration: float,
    # source: stem, clip_start: float, desc?: str}. These get placed
    # BEFORE normal B-roll selection, so they take priority and adjacent
    # B-roll trims around them.
    concept_beats: list[dict] = field(default_factory=list)

    # --- Audio ---
    song: str = ""  # Filename in projects/<slug>/raw/
    song_offset_sec: float = 0.0
    # Baseline song gain before per-cue ducking. Modern hyper-mastered songs
    # (Renegades, most 2015+ pop) need -6 to -8 dB to leave headroom for VO
    # lift; older/quieter masters (In the End choir) are fine at -4.
    song_gain_db: float = -4.0
    # Default duck depth during VO cues. -10 dB keeps the song clearly
    # audible under VO (musical blend). -6 is subtle, -14 is more
    # aggressive. Project-config.yaml can override per-project.
    default_duck_db: float = -10.0

    # --- Template ---
    template: str = "HauntedVeteran"

    # --- Vision prompt context ---
    # Appended to every vision-caption prompt so GPT-4o knows what series
    # this is. E.g. "Resident Evil 9 game cutscene" or "Marvel movie scene".
    vision_context: str = "game cutscene"

    # --- Engine preferences ---
    add_titles: bool = False           # Needs ffmpeg drawtext; off by default
    apply_transitions: bool = True
    add_sfx: bool = True
    run_qa: bool = True
    extract_missing_vo: bool = False   # Off unless project needs fresh extraction
    run_director: bool = False         # Requires EditPlan shape; disabled for LayeredPlan
    build_storyboard: bool = False     # Same: LayeredPlan shape incompatible for now
    build_thumbnail: bool = True
    generate_captions: bool = True
    build_youtube_meta: bool = True
    copyright_audit: bool = True
    enrich_motion: bool = True
    enrich_gaze: bool = True
    target_duration_sec: float | None = None
    export_presets: list[str] = field(default_factory=lambda: ["youtube"])

    # --- Style preferences ---
    cluster_archetype: str = "single-character arc"
    lut_name: str = "cinematic-teal-orange"
    lut_intensity: float = 0.5

    # --- Internal: the on-disk path for this project ---
    _project_dir: Path | None = None
    _config_path: Path | None = None

    # ---- convenience helpers ----

    @property
    def raw_dir(self) -> Path:
        if self._project_dir is None:
            raise RuntimeError("project_dir not set on ProjectConfig")
        return self._project_dir / "raw"

    @property
    def song_path(self) -> Path | None:
        """Return the absolute song path or None when not configured."""
        if not self.song:
            return None
        return self.raw_dir / self.song

    @property
    def dialogue_dir(self) -> Path:
        if self._project_dir is None:
            raise RuntimeError("project_dir not set on ProjectConfig")
        return self._project_dir / "dialogue"

    @property
    def exports_dir(self) -> Path:
        if self._project_dir is None:
            raise RuntimeError("project_dir not set on ProjectConfig")
        return self._project_dir / "exports"

    @property
    def baselines_dir(self) -> Path:
        if self._project_dir is None:
            raise RuntimeError("project_dir not set on ProjectConfig")
        return self._project_dir / "baselines"

    def source_mp4_for_era(self, era_key: str) -> Path | None:
        """Look up the source mp4 for a given era key."""
        if self._project_dir is None:
            return None
        stem = self.era_source_map.get(era_key)
        if not stem:
            return None
        candidate = self.raw_dir / f"{stem}.mp4"
        return candidate if candidate.exists() else None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON/YAML dump (excluding private fields)."""
        out = asdict(self)
        out.pop("_project_dir", None)
        out.pop("_config_path", None)
        return out


# ---------------------------------------------------------------------------
# Loading / saving
# ---------------------------------------------------------------------------


def _deep_merge_defaults(cfg_dict: dict, defaults: dict) -> dict:
    """Fill in missing keys from defaults (recursive for nested dicts)."""
    merged = dict(defaults)
    for k, v in cfg_dict.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            merged[k] = _deep_merge_defaults(v, defaults[k])
        else:
            merged[k] = v
    return merged


def load_project_config(project_dir: str | Path) -> ProjectConfig:
    """Load `project-config.yaml` or `.json` from the project directory.

    Returns a filled-in ProjectConfig. If no config file exists, returns a
    default config with a warning message — the engine will still run but
    behavior may be generic.
    """
    pdir = Path(project_dir)
    if not pdir.exists():
        raise FileNotFoundError(f"Project directory not found: {pdir}")

    candidates = [
        pdir / "project-config.yaml",
        pdir / "project-config.yml",
        pdir / "project-config.json",
    ]
    config_path: Path | None = None
    for c in candidates:
        if c.exists():
            config_path = c
            break

    raw: dict[str, Any] = {}
    if config_path is not None:
        if config_path.suffix in (".yaml", ".yml"):
            if not _YAML_AVAILABLE:
                raise RuntimeError(
                    f"pyyaml is required to load {config_path}. "
                    "Install with: pip install pyyaml"
                )
            with config_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        elif config_path.suffix == ".json":
            with config_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
    else:
        # Use project directory name as character default
        raw = {"character": pdir.name.split("-")[0]}

    # Strip any private fields and any keys the dataclass doesn't know about,
    # so user-added notes/custom fields in YAML don't crash the constructor.
    known_fields = {f.name for f in fields(ProjectConfig) if not f.name.startswith("_")}
    filtered: dict[str, Any] = {}
    unknown: list[str] = []
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if k in known_fields:
            filtered[k] = v
        else:
            unknown.append(k)
    if unknown and config_path is not None:
        logger.warning(
            "project-config %s has unknown keys ignored: %s",
            config_path.name, ", ".join(sorted(unknown)),
        )

    cfg = ProjectConfig(**filtered)
    cfg._project_dir = pdir
    cfg._config_path = config_path
    return cfg


def save_project_config(cfg: ProjectConfig, path: str | Path | None = None) -> Path:
    """Write the config back to disk as YAML (preferred) or JSON."""
    if path is None:
        if cfg._project_dir is None:
            raise ValueError("path required when _project_dir is not set")
        path = cfg._project_dir / "project-config.yaml"
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    data = cfg.to_dict()
    if out.suffix in (".yaml", ".yml"):
        if not _YAML_AVAILABLE:
            raise RuntimeError("pyyaml not installed; cannot write yaml")
        with out.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    else:
        with out.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return out


# ---------------------------------------------------------------------------
# Built-in era-pattern defaults (fallback when project-config is absent)
# ---------------------------------------------------------------------------


# Conservative defaults that match the Leon project. Used only when
# era_source_map is empty and we need era detection to still work.
_DEFAULT_ERA_PATTERNS = {
    "RE2R-1998": r"leon-re2r-cutscenes|re2r|resident[-_ ]evil[-_ ]2[-_ ]remake",
    "RE4R-2004": r"leon-re4r-cutscenes|re4r|resident[-_ ]evil[-_ ]4[-_ ]remake",
    "RE6-2013": r"leon-re6-cutscenes|re6\b|resident[-_ ]evil[-_ ]6",
    "Damnation-2011": r"leon-damnation|damnation",
    "Vendetta-2017": r"leon-vendetta|vendetta",
    "ID-2021": r"leon-infinite-darkness|infinite[-_ ]darkness",
    "RE9-2026": r"re9[-_ ]|resident[-_ ]evil[-_ ]9",
}


def build_era_patterns(cfg: ProjectConfig) -> dict[str, str]:
    """Build a regex dict `{era_key_with_year: source_name_regex}` from config.

    If config has no era_source_map, falls back to the Leon-era defaults
    (backward compatibility).
    """
    if not cfg.era_source_map:
        return dict(_DEFAULT_ERA_PATTERNS)

    out: dict[str, str] = {}
    for era_key, source_stem in cfg.era_source_map.items():
        # Allow pattern to match either the exact stem or dashed/underscored
        # variants of the era_key itself.
        escaped = source_stem.replace("-", r"[-_ ]")
        out[era_key] = rf"{escaped}|\b{era_key.lower()}\b"
    return out


def character_aliases(cfg: ProjectConfig) -> list[str]:
    """Return the primary character name plus any aliases, all lowercased."""
    names = [cfg.character.lower()] + [a.lower() for a in cfg.character_aliases]
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
