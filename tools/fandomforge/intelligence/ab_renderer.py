"""A/B variant renderer for FandomForge.

Takes one EditPlan and up to 3 variant style overrides, runs each through a
thin rendering layer, and produces a VariantBundle containing rendered paths,
thumbnail contact sheets, and a comparison grid.

Variants differ on three independent axes:
- template: which NarrativeTemplate drives pacing (e.g. HauntedVeteran vs
  MultiEraFlashback)
- color: which color preset is applied (teal-orange / desaturated / noir)
- pacing: shot duration multiplier (default / +25% / -25%)

The comparison grid is a 3xN strip of thumbnail frames extracted at four
canonical moments -- opening / midpoint / peak / end -- for each variant.

Rendering defers to the existing assembly pipeline (build_rough_cut). If
FFmpeg is unavailable the module degrades gracefully: it produces per-variant
edit plan JSON files and a metadata-only VariantBundle with no video paths.
"""

from __future__ import annotations

import copy
import json
import logging
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .shot_optimizer import EditPlan, EditPlanMeta, ShotRecord, VOPlacement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color preset definitions
# ---------------------------------------------------------------------------

_COLOR_PRESETS: dict[str, dict[str, Any]] = {
    "teal-orange": {
        "lut_name": "teal_orange_punch",
        "lut_intensity": 0.85,
        "saturation_scale": 1.10,
        "contrast_boost": 0.05,
        "description": "Classic teal-orange film look with punchy contrast",
    },
    "desaturated": {
        "lut_name": "muted_flat",
        "lut_intensity": 0.70,
        "saturation_scale": 0.60,
        "contrast_boost": 0.00,
        "description": "Washed-out muted palette, bleach-bypass feel",
    },
    "noir": {
        "lut_name": "noir_high_contrast",
        "lut_intensity": 0.90,
        "saturation_scale": 0.20,
        "contrast_boost": 0.15,
        "description": "Near-monochrome with deep blacks and crushed highlights",
    },
}

# ---------------------------------------------------------------------------
# Template pacing aliases
# ---------------------------------------------------------------------------

_PACING_VARIANTS: dict[str, float] = {
    "default": 1.00,
    "slower": 1.25,
    "faster": 0.75,
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VariantConfig:
    """Configuration for a single render variant.

    Attributes:
        name: Short identifier, e.g. "variant_a".
        template_name: NarrativeTemplate name override, or None to keep base.
        color_preset: Key from _COLOR_PRESETS, or None to keep base colors.
        pacing_mode: Key from _PACING_VARIANTS.
        description: Human-readable description of what this variant does.
    """

    name: str
    template_name: str | None
    color_preset: str | None
    pacing_mode: str
    description: str


@dataclass
class VariantThumbnails:
    """Extracted thumbnail paths for one variant at canonical moments.

    Attributes:
        variant_name: Which variant these belong to.
        opening: Path to thumbnail at t~5% of total duration.
        midpoint: Path to thumbnail at t~50% of total duration.
        peak: Path to thumbnail at the plan's big_hit_time.
        end: Path to thumbnail at t~90% of total duration.
    """

    variant_name: str
    opening: Path | None
    midpoint: Path | None
    peak: Path | None
    end: Path | None


@dataclass
class VariantResult:
    """One rendered variant.

    Attributes:
        config: The VariantConfig used to produce this variant.
        plan: The EditPlan after applying variant overrides.
        plan_json_path: Path to the serialised variant EditPlan.
        video_path: Path to the rendered MP4, or None if rendering failed.
        thumbnails: Extracted frames for the comparison grid.
        render_warnings: Any non-fatal warnings from the render.
    """

    config: VariantConfig
    plan: EditPlan
    plan_json_path: Path
    video_path: Path | None
    thumbnails: VariantThumbnails
    render_warnings: list[str] = field(default_factory=list)


@dataclass
class ComparisonGrid:
    """Metadata for a side-by-side comparison grid across all variants.

    Attributes:
        grid_image_path: Path to a composited comparison image (if ImageMagick
            or PIL is available). None otherwise.
        variant_names: Ordered list of variant names in the grid.
        moment_labels: Labels for each row: opening, midpoint, peak, end.
        thumbnail_matrix: variant_name -> list[Path | None], one per moment.
    """

    grid_image_path: Path | None
    variant_names: list[str]
    moment_labels: list[str]
    thumbnail_matrix: dict[str, list[Path | None]]


@dataclass
class VariantBundle:
    """Complete output of render_variants().

    Attributes:
        variants: List of up to 3 VariantResult objects.
        comparison_grid: Cross-variant comparison metadata.
        output_dir: Directory where all output files were written.
        vote_ui_stub: Path to a minimal HTML vote UI file.
    """

    variants: list[VariantResult]
    comparison_grid: ComparisonGrid
    output_dir: Path
    vote_ui_stub: Path


# ---------------------------------------------------------------------------
# Plan mutation helpers
# ---------------------------------------------------------------------------

def _apply_color_preset(plan: EditPlan, preset_name: str) -> EditPlan:
    """Stamp color preset metadata into every shot's intent string.

    Args:
        plan: Source EditPlan.
        preset_name: Key in _COLOR_PRESETS.

    Returns:
        New EditPlan with color metadata embedded in each shot's intent.
    """
    preset = _COLOR_PRESETS.get(preset_name, {})
    lut = preset.get("lut_name", "default")
    intensity = preset.get("lut_intensity", 1.0)
    sat = preset.get("saturation_scale", 1.0)

    shots: list[ShotRecord] = []
    for shot in plan.shots:
        tag = f"[color: lut={lut} intensity={intensity:.2f} sat={sat:.2f}]"
        shots.append(ShotRecord(**{
            **asdict(shot),
            "intent": f"{shot.intent} {tag}",
        }))

    return EditPlan(
        shots=shots,
        dialogue_placements=list(plan.dialogue_placements),
        metadata=plan.metadata,
    )


def _apply_pacing(plan: EditPlan, mode: str) -> EditPlan:
    """Scale shot durations by the pacing mode multiplier.

    Also recomputes start times so the timeline stays coherent.

    Args:
        plan: Source EditPlan.
        mode: Key in _PACING_VARIANTS.

    Returns:
        New EditPlan with adjusted durations and start times.
    """
    scale = _PACING_VARIANTS.get(mode, 1.0)
    if abs(scale - 1.0) < 0.001:
        return plan

    shots: list[ShotRecord] = []
    cursor = 0.0
    for shot in plan.shots:
        new_dur = max(0.3, round(shot.duration * scale, 4))
        shots.append(ShotRecord(**{
            **asdict(shot),
            "duration": new_dur,
            "start_time": round(cursor, 4),
        }))
        cursor += new_dur

    return EditPlan(
        shots=shots,
        dialogue_placements=list(plan.dialogue_placements),
        metadata=plan.metadata,
    )


def _build_variant_plan(base_plan: EditPlan, config: VariantConfig) -> EditPlan:
    """Apply all variant overrides to produce the variant's EditPlan.

    Args:
        base_plan: The original unmodified EditPlan.
        config: Variant specification.

    Returns:
        Modified EditPlan for this variant.
    """
    plan = copy.deepcopy(base_plan)

    if config.color_preset:
        plan = _apply_color_preset(plan, config.color_preset)

    if config.pacing_mode and config.pacing_mode != "default":
        plan = _apply_pacing(plan, config.pacing_mode)

    return plan


# ---------------------------------------------------------------------------
# Default variant templates
# ---------------------------------------------------------------------------

_DEFAULT_VARIANT_CONFIGS: list[VariantConfig] = [
    VariantConfig(
        name="variant_a",
        template_name="HauntedVeteran",
        color_preset="teal-orange",
        pacing_mode="default",
        description="Baseline: HauntedVeteran template, teal-orange grade, default pacing",
    ),
    VariantConfig(
        name="variant_b",
        template_name="MultiEraFlashback",
        color_preset="desaturated",
        pacing_mode="faster",
        description="Fast AMV: MultiEraFlashback template, desaturated grade, -25% durations",
    ),
    VariantConfig(
        name="variant_c",
        template_name="HauntedVeteran",
        color_preset="noir",
        pacing_mode="slower",
        description="Slow noir: HauntedVeteran template, noir grade, +25% durations",
    ),
]


# ---------------------------------------------------------------------------
# Thumbnail extraction
# ---------------------------------------------------------------------------

def _extract_thumbnail(
    video_path: Path,
    timestamp_sec: float,
    output_path: Path,
) -> Path | None:
    """Extract a single frame from a video using FFmpeg.

    Args:
        video_path: Source video file.
        timestamp_sec: Position in the video to extract from.
        output_path: Where to write the JPEG thumbnail.

    Returns:
        output_path if successful, None if FFmpeg is unavailable or fails.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss", str(timestamp_sec),
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "3",
                "-vf", "scale=320:-1",
                str(output_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and output_path.exists():
            return output_path
        logger.warning("FFmpeg thumbnail failed for %s at %.1fs", video_path.name, timestamp_sec)
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("FFmpeg not available for thumbnail extraction: %s", exc)
        return None


def _extract_variant_thumbnails(
    result: VariantResult,
    output_dir: Path,
) -> VariantThumbnails:
    """Extract four canonical thumbnails for a variant.

    Moments are: opening (5%), midpoint (50%), peak (big_hit_time), end (90%).

    Args:
        result: VariantResult with video_path and plan populated.
        output_dir: Directory to write thumbnail files.

    Returns:
        VariantThumbnails with paths populated (None for any that fail).
    """
    meta = result.plan.metadata
    total = meta.total_duration_sec

    moments: dict[str, float] = {
        "opening": total * 0.05,
        "midpoint": total * 0.50,
        "peak": meta.big_hit_time,
        "end": total * 0.90,
    }

    paths: dict[str, Path | None] = {}

    for label, t in moments.items():
        thumb_path = output_dir / f"{result.config.name}_{label}.jpg"
        if result.video_path and result.video_path.exists():
            paths[label] = _extract_thumbnail(result.video_path, t, thumb_path)
        else:
            paths[label] = None

    return VariantThumbnails(
        variant_name=result.config.name,
        opening=paths.get("opening"),
        midpoint=paths.get("midpoint"),
        peak=paths.get("peak"),
        end=paths.get("end"),
    )


# ---------------------------------------------------------------------------
# Comparison grid builder
# ---------------------------------------------------------------------------

def _build_comparison_grid(
    results: list[VariantResult],
    output_dir: Path,
) -> ComparisonGrid:
    """Build a side-by-side comparison grid from variant thumbnails.

    Attempts to composite using PIL if available, falls back to metadata-only.

    Args:
        results: All rendered variants with thumbnails populated.
        output_dir: Directory to write the grid image.

    Returns:
        ComparisonGrid with paths and matrix populated.
    """
    moment_labels = ["opening", "midpoint", "peak", "end"]
    variant_names = [r.config.name for r in results]

    matrix: dict[str, list[Path | None]] = {}
    for r in results:
        th = r.thumbnails
        matrix[r.config.name] = [
            th.opening,
            th.midpoint,
            th.peak,
            th.end,
        ]

    grid_path: Path | None = None
    grid_output = output_dir / "comparison_grid.jpg"

    try:
        from PIL import Image  # type: ignore

        all_thumbs: list[list[Image.Image | None]] = []
        thumb_w, thumb_h = 320, 180

        for moment_idx in range(len(moment_labels)):
            row: list[Image.Image | None] = []
            for variant_name in variant_names:
                thumb_path = matrix[variant_name][moment_idx]
                if thumb_path and thumb_path.exists():
                    try:
                        row.append(Image.open(thumb_path).resize((thumb_w, thumb_h)))
                    except Exception:
                        row.append(None)
                else:
                    row.append(None)
            all_thumbs.append(row)

        cols = len(variant_names)
        rows = len(moment_labels)
        padding = 4
        label_height = 20

        grid_w = cols * thumb_w + (cols + 1) * padding
        grid_h = rows * (thumb_h + label_height) + (rows + 1) * padding

        grid_img = Image.new("RGB", (grid_w, grid_h), color=(20, 20, 20))

        try:
            from PIL import ImageDraw, ImageFont  # type: ignore
            draw = ImageDraw.Draw(grid_img)
            font = ImageFont.load_default()
        except Exception:
            draw = None
            font = None

        for row_idx, (moment_label, row_thumbs) in enumerate(zip(moment_labels, all_thumbs)):
            for col_idx, (variant_name, thumb_img) in enumerate(zip(variant_names, row_thumbs)):
                x = padding + col_idx * (thumb_w + padding)
                y = padding + row_idx * (thumb_h + label_height + padding)

                if thumb_img:
                    grid_img.paste(thumb_img, (x, y))
                else:
                    # Placeholder rectangle
                    placeholder = Image.new("RGB", (thumb_w, thumb_h), color=(40, 40, 40))
                    grid_img.paste(placeholder, (x, y))

                if draw and font:
                    label_text = f"{variant_name} / {moment_label}"
                    draw.text((x + 4, y + thumb_h + 2), label_text, fill=(200, 200, 200), font=font)

        grid_img.save(str(grid_output), "JPEG", quality=85)
        grid_path = grid_output
        logger.info("Comparison grid written to %s", grid_output)

    except ImportError:
        logger.info("PIL not available -- comparison grid metadata only (no image)")
    except Exception as exc:
        logger.warning("Could not build comparison grid image: %s", exc)

    return ComparisonGrid(
        grid_image_path=grid_path,
        variant_names=variant_names,
        moment_labels=moment_labels,
        thumbnail_matrix=matrix,
    )


# ---------------------------------------------------------------------------
# Vote UI stub
# ---------------------------------------------------------------------------

def _write_vote_ui(results: list[VariantResult], output_dir: Path) -> Path:
    """Write a minimal single-page HTML vote UI.

    The page shows each variant's description and a vote button. No server
    required -- votes are stored in localStorage and displayed inline.

    Args:
        results: Rendered variants.
        output_dir: Where to write the HTML file.

    Returns:
        Path to the generated HTML file.
    """
    cards_html = ""
    for r in results:
        th = r.thumbnails
        thumb_src = ""
        for candidate in [th.opening, th.midpoint, th.peak, th.end]:
            if candidate and candidate.exists():
                thumb_src = str(candidate)
                break

        video_src = str(r.video_path) if r.video_path and r.video_path.exists() else ""

        cards_html += f"""
    <div class="card" id="card-{r.config.name}">
      <h2>{r.config.name}</h2>
      <p class="desc">{r.config.description}</p>
      {"<video controls width='480' src='" + video_src + "'></video>" if video_src else "<p class='no-video'>(video not rendered)</p>"}
      {"<img src='" + thumb_src + "' width='320' alt='opening frame'>" if thumb_src else ""}
      <div class="vote-row">
        <button onclick="castVote('{r.config.name}')">Vote for this variant</button>
        <span class="vote-count" id="votes-{r.config.name}">0 votes</span>
      </div>
    </div>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FandomForge Variant Vote</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; margin: 0; padding: 2rem; }}
    h1 {{ text-align: center; color: #c8a; margin-bottom: 2rem; }}
    .grid {{ display: flex; gap: 1.5rem; flex-wrap: wrap; justify-content: center; }}
    .card {{ background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 1.5rem; width: 520px; }}
    .card h2 {{ margin: 0 0 0.5rem; color: #8cf; }}
    .desc {{ color: #999; font-size: 0.9rem; margin-bottom: 1rem; }}
    .no-video {{ color: #555; font-style: italic; }}
    .vote-row {{ display: flex; align-items: center; gap: 1rem; margin-top: 1rem; }}
    button {{ background: #334; color: #adf; border: 1px solid #558; border-radius: 4px;
              padding: 0.5rem 1.25rem; cursor: pointer; font-size: 0.9rem; }}
    button:hover {{ background: #446; }}
    button.voted {{ background: #363; border-color: #585; color: #afa; }}
    .vote-count {{ color: #888; font-size: 0.85rem; }}
    .winner {{ border-color: #585; background: #1a2a1a; }}
  </style>
</head>
<body>
  <h1>FandomForge Variant Comparison</h1>
  <div class="grid">
{cards_html}
  </div>
  <script>
    function loadVotes() {{
      try {{ return JSON.parse(localStorage.getItem('ff_votes') || '{{}}'); }}
      catch {{ return {{}}; }}
    }}
    function saveVotes(v) {{ localStorage.setItem('ff_votes', JSON.stringify(v)); }}
    function castVote(name) {{
      const votes = loadVotes();
      votes[name] = (votes[name] || 0) + 1;
      saveVotes(votes);
      renderVotes(votes);
    }}
    function renderVotes(votes) {{
      const max = Math.max(...Object.values(votes).map(Number), 0);
      for (const [name, count] of Object.entries(votes)) {{
        const el = document.getElementById('votes-' + name);
        if (el) el.textContent = count + ' vote' + (count === 1 ? '' : 's');
        const card = document.getElementById('card-' + name);
        if (card) card.classList.toggle('winner', Number(count) === max && max > 0);
      }}
    }}
    renderVotes(loadVotes());
  </script>
</body>
</html>
"""

    ui_path = output_dir / "vote.html"
    ui_path.write_text(html, encoding="utf-8")
    logger.info("Vote UI written to %s", ui_path)
    return ui_path


# ---------------------------------------------------------------------------
# Rendering bridge
# ---------------------------------------------------------------------------

def _attempt_render(
    plan: EditPlan,
    raw_dir: Path | None,
    output_path: Path,
    warnings: list[str],
) -> Path | None:
    """Attempt to render the plan to an MP4 using the assembly pipeline.

    Imports build_rough_cut from assembly lazily so the module can be imported
    without assembly deps installed. If rendering fails for any reason, returns
    None and appends a warning.

    Args:
        plan: The EditPlan to render.
        raw_dir: Directory containing source video files. If None, rendering
            is skipped and a plan-JSON-only bundle is produced.
        output_path: Desired output path for the MP4.
        warnings: Mutable list to append non-fatal warnings to.

    Returns:
        output_path if render succeeded, None otherwise.
    """
    if raw_dir is None:
        warnings.append("raw_dir not provided -- skipping video render")
        return None

    if not raw_dir.exists():
        warnings.append(f"raw_dir does not exist: {raw_dir}")
        return None

    try:
        from fandomforge.assembly.assemble import build_rough_cut  # type: ignore
        from fandomforge.intelligence.nle_export import shots_to_clips  # type: ignore

        clips = shots_to_clips(plan.shots, raw_dir)
        if not clips:
            warnings.append("No usable clips found in raw_dir for this plan")
            return None

        success = build_rough_cut(clips, output_path)
        if success and output_path.exists():
            return output_path
        warnings.append(f"build_rough_cut returned failure for {output_path.name}")
        return None

    except Exception as exc:
        warnings.append(f"Render error: {exc}")
        logger.warning("Render failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_variants(
    base_plan: EditPlan,
    variant_templates: list[VariantConfig] | None = None,
    output_dir: Path | str = Path("output/variants"),
    raw_dir: Path | str | None = None,
) -> VariantBundle:
    """Render up to 3 variants of an EditPlan and produce a comparison bundle.

    Args:
        base_plan: The original EditPlan to use as the starting point.
        variant_templates: List of up to 3 VariantConfig instances. If None
            or empty, uses _DEFAULT_VARIANT_CONFIGS.
        output_dir: Directory to write all outputs. Created if absent.
        raw_dir: Directory containing source video clips for rendering. If
            None, only plan JSONs and vote UI are produced (no MP4s).

    Returns:
        VariantBundle with up to 3 VariantResult objects, a comparison grid,
        and a vote UI HTML stub.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if raw_dir is not None:
        raw_dir = Path(raw_dir)

    configs = (variant_templates or _DEFAULT_VARIANT_CONFIGS)[:3]

    results: list[VariantResult] = []

    for config in configs:
        logger.info("Building variant: %s", config.name)
        variant_warnings: list[str] = []

        # Apply overrides to produce this variant's plan
        variant_plan = _build_variant_plan(base_plan, config)

        # Serialise the plan
        plan_json_path = output_dir / f"{config.name}_plan.json"
        try:
            variant_plan.to_json(plan_json_path)
        except Exception as exc:
            variant_warnings.append(f"Could not save plan JSON: {exc}")

        # Attempt render
        video_output_path = output_dir / f"{config.name}.mp4"
        video_path = _attempt_render(
            variant_plan,
            raw_dir,
            video_output_path,
            variant_warnings,
        )

        result = VariantResult(
            config=config,
            plan=variant_plan,
            plan_json_path=plan_json_path,
            video_path=video_path,
            thumbnails=VariantThumbnails(
                variant_name=config.name,
                opening=None,
                midpoint=None,
                peak=None,
                end=None,
            ),
            render_warnings=variant_warnings,
        )
        results.append(result)

    # Extract thumbnails after render
    thumbs_dir = output_dir / "thumbnails"
    thumbs_dir.mkdir(exist_ok=True)
    for result in results:
        result.thumbnails = _extract_variant_thumbnails(result, thumbs_dir)

    # Build comparison grid
    comparison_grid = _build_comparison_grid(results, output_dir)

    # Build vote UI
    vote_ui_path = _write_vote_ui(results, output_dir)

    # Write bundle metadata
    bundle_meta = {
        "variants": [
            {
                "name": r.config.name,
                "description": r.config.description,
                "template_name": r.config.template_name,
                "color_preset": r.config.color_preset,
                "pacing_mode": r.config.pacing_mode,
                "plan_json": str(r.plan_json_path),
                "video": str(r.video_path) if r.video_path else None,
                "thumbnails": {
                    "opening": str(r.thumbnails.opening) if r.thumbnails.opening else None,
                    "midpoint": str(r.thumbnails.midpoint) if r.thumbnails.midpoint else None,
                    "peak": str(r.thumbnails.peak) if r.thumbnails.peak else None,
                    "end": str(r.thumbnails.end) if r.thumbnails.end else None,
                },
                "warnings": r.render_warnings,
            }
            for r in results
        ],
        "comparison_grid": str(comparison_grid.grid_image_path) if comparison_grid.grid_image_path else None,
        "vote_ui": str(vote_ui_path),
        "output_dir": str(output_dir),
    }
    meta_path = output_dir / "bundle.json"
    meta_path.write_text(json.dumps(bundle_meta, indent=2), encoding="utf-8")
    logger.info("VariantBundle metadata written to %s", meta_path)

    return VariantBundle(
        variants=results,
        comparison_grid=comparison_grid,
        output_dir=output_dir,
        vote_ui_stub=vote_ui_path,
    )
