"""FandomForge CLI — the `ff` command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from fandomforge import __version__
from fandomforge.assembly import (
    ColorPreset,
    apply_base_grade,
    build_rough_cut,
    detect_scenes,
    parse_shot_list,
    trim_silence,
)
from fandomforge.assembly.color_plan import ColorPlan, load_color_plan, save_color_plan
from fandomforge.assembly.dialogue import (
    DialogueEntry,
    entries_to_json,
    load_dialogue_json,
    parse_dialogue_script,
)
from fandomforge.assembly.parser import shots_to_dict
from fandomforge.assembly.scripts import (
    generate_download_script,
    generate_extract_dialogue_script,
)
from fandomforge.audio import analyze_beats, compute_energy_curve, detect_drops
from fandomforge.audio.drops import detect_breakdowns, detect_buildups
from fandomforge.catalog import Catalog, Clip
from fandomforge.intelligence.dialogue_finder import (
    find_dialogue_across_project,
    find_dialogue_in_srt,
    transcribe_with_whisper,
)
from fandomforge.intelligence.lut import apply_lut, build_lut_library, list_available_luts
from fandomforge.intelligence.preview import generate_contact_sheet
from fandomforge.intelligence.color_match import (
    extract_reference_frame,
    histogram_match_video,
)
from fandomforge.intelligence.transitions import (
    generate_dip_to_black,
    generate_flash_stack,
    generate_speed_ramp,
    generate_whip_pan,
)
from fandomforge.intelligence.openai_helper import (
    openai_available,
    rank_shot_descriptions,
    transcribe_via_openai,
)
from fandomforge.intelligence.nle_export import (
    TimelineClip,
    export_edl,
    export_fcpxml,
    shots_to_clips,
)
from fandomforge.intelligence.director import propose_edit
from fandomforge.intelligence.tts import synthesize_speech, OPENAI_VOICES
from fandomforge.intelligence.feedback_loop import (
    FeedbackCorrection,
    apply_feedback,
    load_feedback_from_file,
    save_feedback_to_file,
)
from fandomforge.intelligence.nle_export_pro import export as export_nle_pro
from fandomforge.intelligence.ab_renderer import (
    VariantConfig,
    render_variants,
    _DEFAULT_VARIANT_CONFIGS,
)
from fandomforge.intelligence.style_clustering import (
    cluster_references,
    save_cluster_result,
    get_cluster_template,
)
from fandomforge.intelligence.shot_optimizer import EditPlan
from fandomforge.intelligence.face_filter import (
    encode_reference_face,
    capture_reference_from_video,
    filter_shots_by_face,
    scan_video_for_face,
)
from fandomforge.intelligence.clip_search import (
    build_frame_index,
    semantic_search,
)
from fandomforge.sources import (
    Source,
    SourceCatalog,
    download_source,
    extract_range,
    fetch_transcript,
)
from fandomforge.video import get_video_info
from fandomforge.schemas import SCHEMA_IDS, list_schemas
from fandomforge.validation import (
    ValidationError,
    infer_schema_id,
    validate,
    validate_and_write,
    validate_file,
)

console = Console()


@click.group()
@click.version_option(__version__, prog_name="ff")
def main() -> None:
    """FandomForge — multifandom video creation toolkit."""


# ---------- validate ----------


@main.group()
def validate_cmd() -> None:
    """Validate FandomForge JSON/YAML artifacts against their schemas."""


# `ff validate` is the user-facing name; Click requires a Python identifier so we
# rename via `name="validate"` on registration.
main.add_command(validate_cmd, name="validate")


@validate_cmd.command("file")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--schema",
    "schema_id_flag",
    type=click.Choice(list(SCHEMA_IDS)),
    help="Schema id to validate against. Auto-inferred from filename when omitted.",
)
def validate_file_cmd(path: Path, schema_id_flag: str | None) -> None:
    """Validate a single artifact file. Exits non-zero on any schema violation."""
    try:
        sid = schema_id_flag or infer_schema_id(path)
    except KeyError as ke:
        console.print(f"[red]Cannot infer schema for {path.name}[/red]: {ke}")
        raise SystemExit(2) from ke

    try:
        validate_file(path, sid)
    except ValidationError as ve:
        console.print(f"[red]{path}[/red] failed validation against [yellow]{sid}[/yellow]")
        for failure in ve.failures:
            console.print(f"  [red]✗[/red] {failure.render()}")
        raise SystemExit(1) from ve
    except FileNotFoundError as fnf:
        console.print(f"[red]{fnf}[/red]")
        raise SystemExit(2) from fnf

    console.print(f"[green]✓[/green] {path} is valid ({sid})")


@validate_cmd.command("list")
def validate_list_cmd() -> None:
    """List every known schema id."""
    table = Table(title="FandomForge schemas")
    table.add_column("Schema id")
    table.add_column("File")
    for sid in list_schemas():
        table.add_row(sid, f"fandomforge/schemas/{sid}.schema.json")
    console.print(table)


@validate_cmd.command("project")
@click.argument("project_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def validate_project_cmd(project_dir: Path) -> None:
    """Walk a project directory and validate every artifact it finds.

    Checks (when present): project-config.yaml, beat-map.json, shot-list.json,
    color-plan.json, transition-plan.json, audio-plan.json, title-plan.json,
    edit-plan.json, source-catalog.json, qa-report.json.
    Non-zero exit if any fail.
    """
    candidates: list[tuple[Path, str]] = []
    for rel, sid in [
        ("project-config.yaml", "project-config"),
        ("project-config.yml", "project-config"),
        ("project-config.json", "project-config"),
        ("beat-map.json", "beat-map"),
        ("data/beat-map.json", "beat-map"),
        ("shot-list.json", "shot-list"),
        ("data/shot-list.json", "shot-list"),
        ("color-plan.json", "color-plan"),
        ("data/color-plan.json", "color-plan"),
        ("transition-plan.json", "transition-plan"),
        ("data/transition-plan.json", "transition-plan"),
        ("audio-plan.json", "audio-plan"),
        ("data/audio-plan.json", "audio-plan"),
        ("title-plan.json", "title-plan"),
        ("data/title-plan.json", "title-plan"),
        ("edit-plan.json", "edit-plan"),
        ("data/edit-plan.json", "edit-plan"),
        ("source-catalog.json", "source-catalog"),
        ("data/source-catalog.json", "source-catalog"),
        ("qa-report.json", "qa-report"),
        ("data/qa-report.json", "qa-report"),
    ]:
        p = project_dir / rel
        if p.exists():
            candidates.append((p, sid))

    if not candidates:
        console.print(
            f"[yellow]No known artifacts found under {project_dir}.[/yellow] "
            f"Nothing to validate."
        )
        raise SystemExit(0)

    failures_total = 0
    for path, sid in candidates:
        try:
            validate_file(path, sid)
            console.print(f"[green]✓[/green] {path.relative_to(project_dir)} ({sid})")
        except ValidationError as ve:
            failures_total += len(ve.failures)
            console.print(
                f"[red]✗[/red] {path.relative_to(project_dir)} ({sid}) — "
                f"{len(ve.failures)} failure(s)"
            )
            for f in ve.failures:
                console.print(f"    [red]·[/red] {f.render()}")

    if failures_total:
        console.print(
            f"[red]Project {project_dir.name} failed validation "
            f"({failures_total} total failure(s)).[/red]"
        )
        raise SystemExit(1)
    console.print(f"[green]Project {project_dir.name} is valid.[/green]")


# ---------- ingest ----------


@main.command("ingest")
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--project", "project_dir", type=click.Path(path_type=Path), required=True,
              help="Project directory (will be created if missing).")
@click.option("--fandom", required=True, help="Fandom label (e.g. 'Star Wars').")
@click.option("--source-type", type=click.Choice(["movie", "tv", "anime", "game", "short", "trailer", "other"]),
              default="movie")
@click.option("--title", help="Source title (e.g. 'Revenge of the Sith').")
@click.option("--year", type=int, help="Release year.")
@click.option("--character", "characters", multiple=True,
              help="Character ref: 'Name=path/to/face.jpg'. Repeatable.")
@click.option("--no-transcript", is_flag=True, help="Skip Whisper transcription.")
@click.option("--no-scenes", is_flag=True, help="Skip scene detection.")
@click.option("--no-clip", is_flag=True, help="Skip CLIP frame embeddings.")
@click.option("--no-characters", is_flag=True, help="Skip face-based character tagging.")
@click.option("--whisper-model",
              type=click.Choice(["tiny", "base", "small", "medium", "large"]),
              default="base")
@click.option("--clip-interval", type=float, default=2.0,
              help="Seconds between CLIP-embedded frames (smaller = denser index).")
@click.option("--force", is_flag=True, help="Re-run all steps even if cached.")
def ingest_cmd(
    video: Path,
    project_dir: Path,
    fandom: str,
    source_type: str,
    title: str | None,
    year: int | None,
    characters: tuple[str, ...],
    no_transcript: bool,
    no_scenes: bool,
    no_clip: bool,
    no_characters: bool,
    whisper_model: str,
    clip_interval: float,
    force: bool,
) -> None:
    """Ingest a source video: probe + transcript + scenes + CLIP + characters.

    Example:

        ff ingest raw/rots.mp4 --project projects/mentor-loss \\
            --fandom "Star Wars" --title "Revenge of the Sith" --year 2005 \\
            --character "Obi-Wan=refs/obiwan.jpg"
    """
    from fandomforge.ingest import ingest_source

    char_map: dict[str, Path] = {}
    for entry in characters:
        if "=" not in entry:
            console.print(f"[red]Invalid --character '{entry}'. Use Name=path/to/img.jpg[/red]")
            raise SystemExit(2)
        name, path_str = entry.split("=", 1)
        char_map[name.strip()] = Path(path_str.strip())

    console.print(f"[cyan]Ingesting[/cyan] {video} -> {project_dir}")
    report = ingest_source(
        video_path=video,
        project_dir=project_dir,
        fandom=fandom,
        source_type=source_type,
        title=title,
        year=year,
        characters=char_map or None,
        run_transcript=not no_transcript,
        run_scenes=not no_scenes,
        run_clip=not no_clip,
        run_characters=not no_characters,
        whisper_model=whisper_model,
        clip_interval_sec=clip_interval,
        force=force,
    )

    table = Table(title=f"Ingest report — {report.source_id}")
    table.add_column("Step")
    table.add_column("Status")
    table.add_column("Detail")
    for step in report.steps:
        color = {"ok": "green", "skipped": "yellow", "failed": "red"}.get(step.status, "white")
        table.add_row(step.name, f"[{color}]{step.status}[/{color}]", step.detail)
    console.print(table)

    if not report.succeeded:
        raise SystemExit(1)


# ---------- dialogue clean (demucs) ----------


@main.group("stems")
def stems_group() -> None:
    """Audio stem separation via demucs (vocals / drums / bass / other)."""


@stems_group.command("separate")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), required=True)
@click.option("--model", type=click.Choice(["htdemucs", "htdemucs_ft", "mdx_extra"]),
              default="htdemucs")
@click.option("--vocals-only", is_flag=True,
              help="Fast 2-stem mode: vocals + no_vocals. Best for dialogue cleaning.")
def stems_separate(source: Path, output_dir: Path, model: str, vocals_only: bool) -> None:
    """Separate an audio/video file into vocals/drums/bass/other stems."""
    from fandomforge.audio.stems import separate_stems, demucs_available

    if not demucs_available():
        console.print("[red]demucs not installed. Install fandomforge-tools with torch support.[/red]")
        raise SystemExit(2)

    console.print(f"[cyan]Separating stems[/cyan] model={model} vocals_only={vocals_only}")
    result = separate_stems(source, output_dir, model=model, two_stems="vocals" if vocals_only else None)
    if result is None:
        console.print("[red]Stem separation failed.[/red]")
        raise SystemExit(1)

    table = Table(title="Stems written")
    table.add_column("Stem")
    table.add_column("Path")
    for name, p in [
        ("vocals", result.vocals),
        ("drums", result.drums),
        ("bass", result.bass),
        ("other", result.other),
    ]:
        if p is not None:
            table.add_row(name, str(p))
    console.print(table)


# ---------- credits ----------


@main.group("credit")
def credit_group() -> None:
    """Generate and check project credit blocks."""


@credit_group.command("generate")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--force", is_flag=True, help="Overwrite an existing credits.md")
def credit_generate_cmd(project_dir: Path, force: bool) -> None:
    """Generate credits.md from edit-plan.json + source-catalog.json."""
    from fandomforge.credits import generate_credits
    from fandomforge.validation import validate

    data = project_dir / "data"
    ep_path = data / "edit-plan.json"
    sc_path = data / "source-catalog.json"
    out = data / "credits.md"

    for p, name in [(ep_path, "edit-plan.json"), (sc_path, "source-catalog.json")]:
        if not p.exists():
            console.print(f"[red]Missing {name} at {p}[/red]")
            raise SystemExit(2)

    if out.exists() and not force:
        console.print(f"[yellow]{out} already exists. Pass --force to overwrite.[/yellow]")
        raise SystemExit(2)

    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    sc = json.loads(sc_path.read_text(encoding="utf-8"))
    validate(ep, "edit-plan")
    validate(sc, "source-catalog")

    result = generate_credits(edit_plan=ep, source_catalog=sc, output_path=out)
    console.print(f"[green]Wrote[/green] {result.path}")
    console.print(f"  Song: {result.song_line}")
    console.print(f"  Sources: {len(result.source_lines)}")


# ---------- doctor ----------


@main.command("doctor")
def doctor_cmd() -> None:
    """Run environment diagnostics across every dep FandomForge uses."""
    from fandomforge.doctor import (
        check_dependency_stack,
        check_optional_api_keys,
        check_omnivore,
    )
    from fandomforge.ingest import MODEL_CACHE_ROOT

    console.print(f"[cyan]Cache root:[/cyan] {MODEL_CACHE_ROOT}")
    console.print()

    table = Table(title="Core dependencies")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    table.add_column("Fix")
    all_checks = check_dependency_stack(MODEL_CACHE_ROOT)
    all_ok = True
    for c in all_checks:
        if not c.ok:
            all_ok = False
        mark = "[green]✓[/green]" if c.ok else "[red]✗[/red]"
        table.add_row(c.name, mark, c.detail, c.fix_hint if not c.ok else "")
    console.print(table)

    table2 = Table(title="API keys (optional)")
    table2.add_column("Key")
    table2.add_column("Status")
    table2.add_column("Purpose")
    for c in check_optional_api_keys():
        mark = "[green]set[/green]" if c.ok else "[yellow]not set[/yellow]"
        table2.add_row(c.name, mark, c.fix_hint)
    console.print(table2)

    table3 = Table(title="Sibling tools")
    table3.add_column("Tool")
    table3.add_column("Status")
    table3.add_column("Detail")
    c = check_omnivore()
    mark = "[green]✓[/green]" if c.ok else "[yellow]not available[/yellow]"
    table3.add_row(c.name, mark, c.detail)
    console.print(table3)

    if not all_ok:
        console.print(
            "[yellow]Some dependencies are missing. Fix the items marked ✗ "
            "then re-run `ff doctor`.[/yellow]"
        )
        raise SystemExit(1)
    console.print("[green]All systems green.[/green]")


# ---------- lore ----------


@main.group()
def lore() -> None:
    """Fetch and search fandom lore via Omnivore."""


@lore.command("fetch")
@click.argument("url")
@click.option("--tag", "tags", multiple=True, help="Extra tags to apply. Repeatable.")
@click.option("--timeout", type=int, default=180)
def lore_fetch_cmd(url: str, tags: tuple[str, ...], timeout: int) -> None:
    """Fetch a wiki/lore URL into the Omnivore catalog."""
    from fandomforge.integrations.omnivore import fetch_lore, omnivore_available

    if not omnivore_available():
        console.print("[red]Omnivore not available. `ff doctor` will say where to install it.[/red]")
        raise SystemExit(2)
    res = fetch_lore(url, tags=list(tags), timeout_sec=timeout)
    if res.stdout:
        console.print(res.stdout)
    if res.stderr:
        console.print(f"[yellow]{res.stderr}[/yellow]")
    if not res.ok:
        raise SystemExit(res.exit_code)


@lore.command("search")
@click.argument("query")
@click.option("--tag", default="fandomforge", help="Tag to filter by. Default 'fandomforge'.")
@click.option("--limit", type=int, default=20)
def lore_search_cmd(query: str, tag: str, limit: int) -> None:
    from fandomforge.integrations.omnivore import omnivore_available, search_catalog

    if not omnivore_available():
        console.print("[red]Omnivore not available.[/red]")
        raise SystemExit(2)
    res = search_catalog(query, tag=tag, limit=limit)
    console.print(res.stdout or "[yellow]no hits[/yellow]")
    if res.stderr:
        console.print(f"[yellow]{res.stderr}[/yellow]")


# ---------- export project ----------


@main.group()
def export() -> None:
    """Export full NLE projects (Resolve / Premiere / FCP / CapCut / Vegas)."""


def _load_project_artifacts(project_dir: Path) -> dict[str, Any]:
    """Load every artifact we need for export. Validates each before use."""
    data = project_dir / "data"
    artifacts: dict[str, Any] = {}
    for name, sid in [
        ("edit-plan", "edit-plan"),
        ("beat-map", "beat-map"),
        ("shot-list", "shot-list"),
        ("source-catalog", "source-catalog"),
        ("color-plan", "color-plan"),
        ("transition-plan", "transition-plan"),
        ("audio-plan", "audio-plan"),
        ("title-plan", "title-plan"),
        ("qa-report", "qa-report"),
    ]:
        p = data / f"{name}.json"
        if p.exists():
            from fandomforge.validation import validate as _v
            loaded = json.loads(p.read_text(encoding="utf-8"))
            _v(loaded, sid)
            artifacts[name] = loaded
    # Required minimums.
    for must in ("shot-list", "source-catalog", "edit-plan"):
        if must not in artifacts:
            raise SystemExit(
                f"[red]Missing required {must}.json under {data}. Run `ff qa gate` first.[/red]"
            )
    return artifacts


@export.group("project")
def export_project_group() -> None:
    """Generate a complete NLE project ready to open."""


@export_project_group.command("resolve")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--portable", is_flag=True,
              help="Skip the native Resolve scripting path even if available.")
@click.option("--skip-qa-gate", is_flag=True,
              help="Don't require qa-report.json. NOT recommended.")
def export_resolve_cmd(project_dir: Path, portable: bool, skip_qa_gate: bool) -> None:
    from fandomforge.assembly.resolve_project import export_resolve_project
    artifacts = _load_project_artifacts(project_dir)
    _require_passing_qa(artifacts, skip_qa_gate)
    _require_credits(project_dir, skip_qa_gate)
    result = export_resolve_project(
        project_dir=project_dir,
        shot_list=artifacts["shot-list"],
        source_catalog=artifacts["source-catalog"],
        edit_plan=artifacts["edit-plan"],
        audio_plan=artifacts.get("audio-plan"),
        color_plan=artifacts.get("color-plan"),
        title_plan=artifacts.get("title-plan"),
        beat_map=artifacts.get("beat-map"),
        qa_report=artifacts.get("qa-report"),
        force_portable=portable,
    )
    console.print(f"[green]Resolve export[/green] -> {result.project_dir}")
    if result.native_project_created:
        console.print(f"  Native Resolve project: [cyan]{result.native_project_name}[/cyan]")
    console.print(f"  FCPXML: {result.fcpxml_result.fcpxml_path}")
    for w in result.warnings:
        console.print(f"  [yellow]warn[/yellow]: {w}")


@export_project_group.command("premiere")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--skip-qa-gate", is_flag=True)
def export_premiere_cmd(project_dir: Path, skip_qa_gate: bool) -> None:
    from fandomforge.assembly.premiere_project import export_premiere_project
    artifacts = _load_project_artifacts(project_dir)
    _require_passing_qa(artifacts, skip_qa_gate)
    _require_credits(project_dir, skip_qa_gate)
    result = export_premiere_project(
        project_dir=project_dir,
        shot_list=artifacts["shot-list"],
        source_catalog=artifacts["source-catalog"],
        edit_plan=artifacts["edit-plan"],
        audio_plan=artifacts.get("audio-plan"),
        color_plan=artifacts.get("color-plan"),
        title_plan=artifacts.get("title-plan"),
        beat_map=artifacts.get("beat-map"),
        qa_report=artifacts.get("qa-report"),
    )
    console.print(f"[green]Premiere export[/green] -> {result.project_dir}")
    console.print(f"  FCPXML: {result.fcpxml_result.fcpxml_path}")
    console.print(f"  JSX import script: {result.jsx_path}")


@export_project_group.command("fcp")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--skip-qa-gate", is_flag=True)
def export_fcp_cmd(project_dir: Path, skip_qa_gate: bool) -> None:
    from fandomforge.assembly.fcp_project import export_fcp_project
    artifacts = _load_project_artifacts(project_dir)
    _require_passing_qa(artifacts, skip_qa_gate)
    _require_credits(project_dir, skip_qa_gate)
    result = export_fcp_project(
        project_dir=project_dir,
        shot_list=artifacts["shot-list"],
        source_catalog=artifacts["source-catalog"],
        edit_plan=artifacts["edit-plan"],
        audio_plan=artifacts.get("audio-plan"),
        color_plan=artifacts.get("color-plan"),
        title_plan=artifacts.get("title-plan"),
        beat_map=artifacts.get("beat-map"),
        qa_report=artifacts.get("qa-report"),
    )
    console.print(f"[green]FCP export[/green] -> {result.bundle_dir}")


@export_project_group.command("capcut")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--skip-qa-gate", is_flag=True)
def export_capcut_cmd(project_dir: Path, skip_qa_gate: bool) -> None:
    from fandomforge.assembly.capcut_project import export_capcut_project
    artifacts = _load_project_artifacts(project_dir)
    _require_passing_qa(artifacts, skip_qa_gate)
    _require_credits(project_dir, skip_qa_gate)
    result = export_capcut_project(
        project_dir=project_dir,
        shot_list=artifacts["shot-list"],
        source_catalog=artifacts["source-catalog"],
        edit_plan=artifacts["edit-plan"],
        audio_plan=artifacts.get("audio-plan"),
        color_plan=artifacts.get("color-plan"),
        title_plan=artifacts.get("title-plan"),
        beat_map=artifacts.get("beat-map"),
        qa_report=artifacts.get("qa-report"),
    )
    console.print(f"[green]CapCut export[/green] -> {result.project_dir}")
    console.print(f"  Draft folder: {result.draft_dir}")


@export_project_group.command("vegas")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--skip-qa-gate", is_flag=True)
def export_vegas_cmd(project_dir: Path, skip_qa_gate: bool) -> None:
    from fandomforge.assembly.vegas_project import export_vegas_project
    artifacts = _load_project_artifacts(project_dir)
    _require_passing_qa(artifacts, skip_qa_gate)
    _require_credits(project_dir, skip_qa_gate)
    result = export_vegas_project(
        project_dir=project_dir,
        shot_list=artifacts["shot-list"],
        source_catalog=artifacts["source-catalog"],
        edit_plan=artifacts["edit-plan"],
        audio_plan=artifacts.get("audio-plan"),
        color_plan=artifacts.get("color-plan"),
        title_plan=artifacts.get("title-plan"),
        beat_map=artifacts.get("beat-map"),
        qa_report=artifacts.get("qa-report"),
    )
    console.print(f"[green]Vegas export[/green] -> {result.project_dir}")


@export_project_group.command("all")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("--skip-qa-gate", is_flag=True)
def export_all_cmd(project_dir: Path, skip_qa_gate: bool) -> None:
    """Generate every NLE project in one pass."""
    from fandomforge.assembly.resolve_project import export_resolve_project
    from fandomforge.assembly.premiere_project import export_premiere_project
    from fandomforge.assembly.fcp_project import export_fcp_project
    from fandomforge.assembly.capcut_project import export_capcut_project
    from fandomforge.assembly.vegas_project import export_vegas_project

    artifacts = _load_project_artifacts(project_dir)
    _require_passing_qa(artifacts, skip_qa_gate)
    _require_credits(project_dir, skip_qa_gate)
    common = dict(
        project_dir=project_dir,
        shot_list=artifacts["shot-list"],
        source_catalog=artifacts["source-catalog"],
        edit_plan=artifacts["edit-plan"],
        audio_plan=artifacts.get("audio-plan"),
        color_plan=artifacts.get("color-plan"),
        title_plan=artifacts.get("title-plan"),
        beat_map=artifacts.get("beat-map"),
        qa_report=artifacts.get("qa-report"),
    )
    for name, fn in [
        ("Resolve", export_resolve_project),
        ("Premiere", export_premiere_project),
        ("FCP", export_fcp_project),
        ("CapCut", export_capcut_project),
        ("Vegas", export_vegas_project),
    ]:
        console.print(f"[cyan]Exporting {name}...[/cyan]")
        try:
            fn(**common)
            console.print(f"  [green]ok[/green]")
        except Exception as e:
            console.print(f"  [red]failed: {e}[/red]")


def _require_passing_qa(artifacts: dict[str, Any], skip: bool) -> None:
    qr = artifacts.get("qa-report")
    if skip:
        return
    if not qr:
        console.print(
            "[red]qa-report.json is missing. Run `ff qa gate --project <dir>` first, "
            "or pass --skip-qa-gate to export anyway.[/red]"
        )
        raise SystemExit(2)
    if qr["status"] == "fail":
        console.print(
            f"[red]qa-report.json status is FAIL. Fix the failing rules or "
            f"pass --skip-qa-gate.[/red]"
        )
        raise SystemExit(2)


def _require_credits(project_dir: Path, skip: bool) -> None:
    if skip:
        return
    credits_path = project_dir / "data" / "credits.md"
    if not credits_path.exists():
        console.print(
            f"[red]credits.md is missing at {credits_path}. Run "
            f"`ff credit generate --project {project_dir}` first, or pass "
            f"--skip-qa-gate to bypass both QA and credits.[/red]"
        )
        raise SystemExit(2)


# ---------- qa ----------


@main.group()
def qa() -> None:
    """Pre-export QA gate and post-render sampling."""


@qa.command("gate")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True, help="Project directory (reads data/).")
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path),
              help="Path to write qa-report.json. Defaults to <project>/data/qa-report.json.")
@click.option("--override", "overrides", multiple=True,
              help="Override a failing rule: 'qa.rule_id=Reason text'. Repeatable.")
@click.option("--stage", type=click.Choice(["pre-export", "post-render"]), default="pre-export")
def qa_gate(
    project_dir: Path,
    output_path: Path | None,
    overrides: tuple[str, ...],
    stage: str,
) -> None:
    """Run the mandatory QA gate. Exits non-zero if any blocking rule fails."""
    from fandomforge.qa import run_gate

    override_map: dict[str, str] = {}
    for entry in overrides:
        if "=" not in entry:
            console.print(f"[red]Invalid --override '{entry}'. Use qa.rule_id=Reason[/red]")
            raise SystemExit(2)
        key, reason = entry.split("=", 1)
        override_map[key.strip()] = reason.strip()

    out = output_path or project_dir / "data" / "qa-report.json"
    report = run_gate(project_dir, overrides=override_map, stage=stage, write_to=out)

    status = report["status"]
    color = {"pass": "green", "warn": "yellow", "fail": "red"}[status]
    console.print(f"[{color}]QA gate: {status.upper()}[/{color}]  ({out})")

    table = Table(title="Rules")
    table.add_column("Id")
    table.add_column("Name")
    table.add_column("Level")
    table.add_column("Status")
    table.add_column("Message")
    for rule in report["rules"]:
        status_color = {
            "pass": "green", "warn": "yellow", "fail": "red",
            "skipped": "dim white", "overridden": "cyan",
        }.get(rule["status"], "white")
        table.add_row(
            rule["id"],
            rule["name"],
            rule["level"],
            f"[{status_color}]{rule['status']}[/{status_color}]",
            rule.get("message", ""),
        )
    console.print(table)

    if status == "fail":
        raise SystemExit(1)


# ---------- match (shot-to-beat, transitions, color) ----------


@main.group()
def match() -> None:
    """Automatic shot / transition / color matching."""


@match.command("shots")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True, help="Project directory (reads data/).")
@click.option("--beat-map", "beat_map_path", type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.option("--source-catalog", "source_catalog_path",
              type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.option("--edit-plan", "edit_plan_path",
              type=click.Path(path_type=Path, dir_okay=False, exists=True))
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path),
              help="Path to write shot-list.json. Defaults to <project>/data/shot-list.json.")
@click.option("--exclude-cliche/--flag-cliche", default=False,
              help="Drop cliche candidates entirely vs. keep but flag (default flag).")
def match_shots_cmd(
    project_dir: Path,
    beat_map_path: Path | None,
    source_catalog_path: Path | None,
    edit_plan_path: Path | None,
    output_path: Path | None,
    exclude_cliche: bool,
) -> None:
    """Match beat slots to source clips using CLIP + scoring."""
    from fandomforge.intelligence.shot_matcher import MatchConfig, match_shots_from_files

    data = project_dir / "data"
    bm = beat_map_path or data / "beat-map.json"
    sc = source_catalog_path or data / "source-catalog.json"
    ep = edit_plan_path or data / "edit-plan.json"
    out = output_path or data / "shot-list.json"

    for p, label in [(bm, "beat-map.json"), (sc, "source-catalog.json"), (ep, "edit-plan.json")]:
        if not p.exists():
            console.print(f"[red]Missing {label} at {p}[/red]")
            raise SystemExit(2)

    cfg = MatchConfig(exclude_cliche=exclude_cliche)
    shot_list = match_shots_from_files(
        project_dir=project_dir,
        beat_map_path=bm,
        source_catalog_path=sc,
        edit_plan_path=ep,
        output_path=out,
        config=cfg,
    )
    console.print(f"[green]Wrote[/green] {out} ({len(shot_list['shots'])} shots, "
                  f"{len(shot_list.get('rejected', []))} rejected)")


@match.command("transitions")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path))
def match_transitions_cmd(project_dir: Path, output_path: Path | None) -> None:
    """Pick a transition type for every cut using optical-flow direction."""
    from fandomforge.intelligence.transition_matcher import match_transitions_from_files

    data = project_dir / "data"
    shots = data / "shot-list.json"
    catalog = data / "source-catalog.json"
    beat = data / "beat-map.json"
    out = output_path or data / "transition-plan.json"

    for p, label in [(shots, "shot-list.json"), (catalog, "source-catalog.json")]:
        if not p.exists():
            console.print(f"[red]Missing {label} at {p}[/red]")
            raise SystemExit(2)

    plan = match_transitions_from_files(
        shot_list_path=shots,
        source_catalog_path=catalog,
        beat_map_path=beat if beat.exists() else None,
        output_path=out,
    )
    counts: dict[str, int] = {}
    for t in plan["transitions"]:
        counts[t["type"]] = counts.get(t["type"], 0) + 1
    console.print(f"[green]Wrote[/green] {out} ({len(plan['transitions'])} transitions)")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        console.print(f"  {k}: {v}")


@match.command("color")
@click.option("--project", "project_dir", type=click.Path(path_type=Path, file_okay=False),
              required=True)
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path))
@click.option("--global-lut", type=click.Path(path_type=Path, dir_okay=False),
              help="Path to a project-wide .cube LUT applied after per-source match.")
def match_color_cmd(project_dir: Path, output_path: Path | None,
                    global_lut: Path | None) -> None:
    """Derive a per-source color plan from a hero reference frame."""
    from fandomforge.intelligence.color_matcher import (
        ColorMatchConfig,
        match_color_from_files,
    )

    data = project_dir / "data"
    shots = data / "shot-list.json"
    catalog = data / "source-catalog.json"
    out = output_path or data / "color-plan.json"

    for p, label in [(shots, "shot-list.json"), (catalog, "source-catalog.json")]:
        if not p.exists():
            console.print(f"[red]Missing {label} at {p}[/red]")
            raise SystemExit(2)

    cfg = ColorMatchConfig(global_lut=str(global_lut) if global_lut else None)
    plan = match_color_from_files(
        shot_list_path=shots,
        source_catalog_path=catalog,
        output_path=out,
        output_dir=project_dir,
        config=cfg,
    )
    console.print(f"[green]Wrote[/green] {out} "
                  f"({len(plan['per_source'])} per-source nodes, hero={plan['hero_frame']['source_id']})")


# ---------- beat ----------

@main.group()
def beat() -> None:
    """Audio beat and drop analysis."""


@beat.command("analyze")
@click.argument("audio", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write beat-map.json to this path")
@click.option("--song", help="Song display name")
@click.option("--artist", help="Artist name")
@click.option("--tempo-hint", type=float, help="BPM hint to constrain search")
@click.option("--tightness", type=int, default=100, help="Beat tracker tightness (higher = stricter)")
@click.option("--beats-per-bar", type=int, default=4, help="Beats per bar (default 4 for 4/4)")
@click.option("--snare-bias", is_flag=True, help="Weight high-frequency flux higher for drop detection")
def beat_analyze(
    audio: Path,
    output: Path | None,
    song: str | None,
    artist: str | None,
    tempo_hint: float | None,
    tightness: int,
    beats_per_bar: int,
    snare_bias: bool,
) -> None:
    """Full beat analysis: BPM, beats, downbeats, drops, buildups, energy curve."""
    console.print(f"[cyan]Analyzing[/cyan] {audio}")

    beat_map = analyze_beats(
        audio,
        song_name=song,
        artist=artist,
        tempo_hint=tempo_hint,
        tightness=tightness,
        beats_per_bar=beats_per_bar,
    )

    console.print(f"  BPM: [yellow]{beat_map.bpm}[/yellow] (confidence {beat_map.bpm_confidence})")
    console.print(f"  Duration: {beat_map.duration_sec:.2f}s")
    console.print(f"  Beats detected: {len(beat_map.beats)}")
    console.print(f"  Downbeats: {len(beat_map.downbeats)}")

    console.print("[cyan]Detecting drops...[/cyan]")
    drops = detect_drops(audio, snare_bias=snare_bias)
    console.print(f"  Drops detected: {len(drops)}")
    for d in drops:
        console.print(f"    [magenta]{d.type}[/magenta] at {d.time:.2f}s (intensity {d.intensity:.2f})")

    console.print("[cyan]Detecting buildups...[/cyan]")
    buildups = detect_buildups(audio, drops)
    console.print(f"  Buildups detected: {len(buildups)}")

    console.print("[cyan]Detecting breakdowns...[/cyan]")
    breakdowns = detect_breakdowns(audio)
    console.print(f"  Breakdowns detected: {len(breakdowns)}")

    console.print("[cyan]Building energy curve...[/cyan]")
    energy_curve = compute_energy_curve(audio)

    from datetime import datetime, timezone

    bm_dict = beat_map.to_dict()
    # downbeat_source is part of the BeatMap dataclass; keep as-is at root.
    result = {
        "schema_version": 1,
        **bm_dict,
        "drops": [d.to_dict() for d in drops],
        "buildups": [b.to_dict() for b in buildups],
        "breakdowns": [bd.to_dict() for bd in breakdowns],
        "energy_curve": [[t, e] for t, e in energy_curve],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": f"ff beat analyze ({__version__})",
    }

    try:
        validate(result, "beat-map")
    except ValidationError as ve:
        console.print("[red]Beat map failed schema validation:[/red]")
        console.print(ve.render())
        raise SystemExit(2) from ve

    if output:
        validate_and_write(result, "beat-map", output)
        console.print(f"[green]Wrote[/green] {output}")
    else:
        click.echo(json.dumps(result, indent=2))


@beat.command("drops")
@click.argument("audio", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--snare-bias", is_flag=True)
def beat_drops(audio: Path, snare_bias: bool) -> None:
    """Detect drops only."""
    drops = detect_drops(audio, snare_bias=snare_bias)
    if not drops:
        console.print("[red]No drops detected.[/red]")
        return
    table = Table(title=f"Drops in {audio.name}")
    table.add_column("Type")
    table.add_column("Time (s)", justify="right")
    table.add_column("Intensity", justify="right")
    for d in drops:
        table.add_row(d.type, f"{d.time:.2f}", f"{d.intensity:.2f}")
    console.print(table)


@beat.command("bpm")
@click.argument("audio", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--tempo-hint", type=float)
def beat_bpm(audio: Path, tempo_hint: float | None) -> None:
    """Quick BPM check."""
    bm = analyze_beats(audio, tempo_hint=tempo_hint)
    console.print(f"[yellow]{bm.bpm} BPM[/yellow]  (confidence {bm.bpm_confidence})")


# ---------- video ----------

@main.group()
def video() -> None:
    """Video metadata commands."""


@video.command("info")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def video_info(path: Path) -> None:
    """Show metadata for a video file."""
    info = get_video_info(path)
    table = Table(title=f"Video info — {path.name}")
    table.add_column("Field")
    table.add_column("Value")
    for k, v in asdict(info).items():
        table.add_row(k, str(v))
    console.print(table)


# ---------- catalog ----------

@main.group()
def catalog() -> None:
    """Clip catalog commands."""


def _catalog_path(project: str | None) -> Path:
    """Find the catalog file for the given project, or the global one."""
    if project:
        return Path("projects") / project / "catalog.json"
    return Path("projects") / "_global" / "catalog.json"


@catalog.command("add")
@click.option("--project", help="Project slug (omit for global catalog)")
@click.option("--source", required=True, help="Source title, e.g. 'Revenge of the Sith'")
@click.option("--fandom", required=True, help="Fandom label, e.g. 'Star Wars'")
@click.option("--timestamp", required=True, help="HH:MM:SS in the source")
@click.option("--duration", type=float, default=2.0, help="Intended clip duration (seconds)")
@click.option("--description", required=True, help="What the shot is")
@click.option("--type", "source_type", default="movie", type=click.Choice(["movie", "tv", "anime", "game", "other"]))
@click.option("--mood", multiple=True, help="Mood tag (repeatable)")
@click.option("--framing", default="", help="wide, MCU, CU, etc.")
@click.option("--motion", default="", help="static, push-in, whip pan, etc.")
@click.option("--color", "color_notes", default="", help="color notes")
@click.option("--notes", default="", help="freeform notes")
def catalog_add(
    project: str | None,
    source: str,
    fandom: str,
    timestamp: str,
    duration: float,
    description: str,
    source_type: str,
    mood: tuple[str, ...],
    framing: str,
    motion: str,
    color_notes: str,
    notes: str,
) -> None:
    """Add a clip to the catalog."""
    cat = Catalog(_catalog_path(project))
    clip = Clip(
        id=f"c_{uuid.uuid4().hex[:8]}",
        source_title=source,
        source_type=source_type,
        fandom=fandom,
        timestamp=timestamp,
        duration_sec=duration,
        description=description,
        mood_tags=list(mood),
        framing=framing,
        motion=motion,
        color_notes=color_notes,
        notes=notes,
    )
    cat.add(clip)
    console.print(f"[green]Added[/green] {clip.id}: {source} @ {timestamp} ({fandom})")


@catalog.command("list")
@click.option("--project", help="Project slug (omit for global catalog)")
@click.option("--fandom", help="Filter by fandom")
@click.option("--mood", help="Filter by mood tag")
@click.option("--search", help="Full-text search")
def catalog_list(project: str | None, fandom: str | None, mood: str | None, search: str | None) -> None:
    """List clips in the catalog."""
    cat = Catalog(_catalog_path(project))
    if fandom:
        clips = cat.find_by_fandom(fandom)
    elif mood:
        clips = cat.find_by_mood(mood)
    elif search:
        clips = cat.search(search)
    else:
        clips = cat.all()

    if not clips:
        console.print("[yellow]No clips found.[/yellow]")
        return

    table = Table(title=f"Clips ({len(clips)})")
    table.add_column("ID")
    table.add_column("Fandom")
    table.add_column("Source")
    table.add_column("Timestamp")
    table.add_column("Description")
    table.add_column("Mood")
    for c in clips:
        table.add_row(
            c.id,
            c.fandom,
            c.source_title,
            c.timestamp,
            c.description[:40] + ("…" if len(c.description) > 40 else ""),
            ", ".join(c.mood_tags),
        )
    console.print(table)


@catalog.command("remove")
@click.argument("clip_id")
@click.option("--project", help="Project slug (omit for global catalog)")
def catalog_remove(clip_id: str, project: str | None) -> None:
    """Remove a clip from the catalog."""
    cat = Catalog(_catalog_path(project))
    if cat.remove(clip_id):
        console.print(f"[green]Removed[/green] {clip_id}")
    else:
        console.print(f"[red]Not found:[/red] {clip_id}")
        sys.exit(1)


# ---------- project ----------

@main.group()
def project() -> None:
    """Project management."""


@project.command("new")
@click.argument("slug")
@click.option("--theme", default="", help="One-sentence theme")
@click.option("--song", default="", help="Song name")
@click.option("--artist", default="", help="Artist name")
def project_new(slug: str, theme: str, song: str, artist: str) -> None:
    """Create a new project directory from templates."""
    proj_dir = Path("projects") / slug
    if proj_dir.exists():
        console.print(f"[red]Project already exists:[/red] {proj_dir}")
        sys.exit(1)
    proj_dir.mkdir(parents=True)

    templates_root = Path("templates")
    copies = [
        ("edit-plan/edit-plan.template.md", "edit-plan.md"),
        ("shot-list/shot-list.template.md", "shot-list.md"),
        ("beat-map/beat-map.template.md", "beat-map.md"),
    ]
    for src_rel, dst_name in copies:
        src = templates_root / src_rel
        if not src.exists():
            console.print(f"[yellow]Missing template:[/yellow] {src}")
            continue
        content = src.read_text()
        content = content.replace("{{PROJECT_NAME}}", slug)
        if theme:
            content = content.replace("{{ONE_SENTENCE_THEME}}", theme)
            content = content.replace("{{THEME}}", theme)
        if song:
            content = content.replace("{{SONG}}", song)
            content = content.replace("{{SONG_TITLE}}", song)
        if artist:
            content = content.replace("{{ARTIST}}", artist)
        (proj_dir / dst_name).write_text(content)

    console.print(f"[green]Created project[/green] {proj_dir}")
    console.print(f"  Start with: [cyan]cd {proj_dir} && $EDITOR edit-plan.md[/cyan]")


# ---------- sources ----------

@main.group()
def sources() -> None:
    """External video source management (download, extract, transcripts)."""


def _source_catalog_path(project: str) -> Path:
    """Find sources.json. New layout: data/sources.json. Legacy: sources.json. Defaults to new."""
    proj = Path("projects") / project
    for candidate in [proj / "data" / "sources.json", proj / "sources.json"]:
        if candidate.exists():
            return candidate
    return proj / "data" / "sources.json"


@sources.command("list")
@click.option("--project", required=True, help="Project slug")
@click.option("--priority", help="Filter by priority (primary / secondary / backup / etc.)")
@click.option("--contains", help="Filter by character/tag appearing in the source")
def sources_list(project: str, priority: str | None, contains: str | None) -> None:
    """List all sources in a project."""
    cat = SourceCatalog(_source_catalog_path(project))
    if priority:
        entries = cat.by_priority(priority)
    elif contains:
        entries = cat.containing(contains)
    else:
        entries = cat.all()

    if not entries:
        console.print("[yellow]No sources found.[/yellow]")
        return

    table = Table(title=f"Sources ({len(entries)})")
    table.add_column("ID")
    table.add_column("Game")
    table.add_column("Duration", justify="right")
    table.add_column("Priority")
    table.add_column("Channel")
    table.add_column("Contains")
    for s in entries:
        table.add_row(
            s.id,
            s.game[:28] + ("…" if len(s.game) > 28 else ""),
            s.duration,
            s.priority,
            s.channel[:20] + ("…" if len(s.channel) > 20 else ""),
            ", ".join(s.contains[:3]) + ("…" if len(s.contains) > 3 else ""),
        )
    console.print(table)


@sources.command("show")
@click.option("--project", required=True)
@click.argument("source_id")
def sources_show(project: str, source_id: str) -> None:
    """Show details for a single source."""
    cat = SourceCatalog(_source_catalog_path(project))
    src = cat.get(source_id)
    if not src:
        console.print(f"[red]Not found:[/red] {source_id}")
        sys.exit(1)
    for k, v in src.to_dict().items():
        console.print(f"[cyan]{k}:[/cyan] {v}")


@sources.command("download")
@click.option("--project", required=True)
@click.option(
    "--resolution",
    default="1080",
    help="Max resolution (e.g. 1080, 720, best)",
)
@click.option("--no-subs", is_flag=True, help="Skip subtitle download")
@click.option("--all", "download_all", is_flag=True, help="Download all primary sources")
@click.option(
    "--priority",
    help="Download all sources with this priority (primary / secondary / ...)",
)
@click.argument("source_ids", nargs=-1)
def sources_download(
    project: str,
    resolution: str,
    no_subs: bool,
    download_all: bool,
    priority: str | None,
    source_ids: tuple[str, ...],
) -> None:
    """Download one or more sources via yt-dlp.

    Usage:
        ff sources download --project leon-edit re4r-all-glp
        ff sources download --project leon-edit --priority primary
        ff sources download --project leon-edit --all
    """
    cat = SourceCatalog(_source_catalog_path(project))

    if download_all:
        targets = cat.by_priority("primary")
    elif priority:
        targets = cat.by_priority(priority)
    else:
        targets = [cat.get(sid) for sid in source_ids if cat.get(sid) is not None]

    if not targets:
        console.print("[yellow]No sources selected.[/yellow]")
        sys.exit(1)

    out_dir = Path("projects") / project / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    for src in targets:
        if src is None or not src.url.startswith(("http://", "https://")):
            console.print(f"[yellow]Skipping non-URL source:[/yellow] {src.id if src else '?'}")
            continue
        console.print(f"[cyan]Downloading[/cyan] {src.id} → {src.title}")
        result = download_source(
            src.url,
            out_dir,
            filename=src.id,
            resolution=resolution,
            write_subs=not no_subs,
            auto_subs=not no_subs,
        )
        if result.success:
            console.print(f"  [green]✓[/green] {result.path}")
        else:
            console.print(f"  [red]✗[/red] {result.stderr.splitlines()[-1] if result.stderr else 'failed'}")


@sources.command("extract")
@click.option("--project", required=True)
@click.option("--source", "source_id", required=True, help="Source ID the clip comes from")
@click.option("--start", required=True, help="Start timestamp (HH:MM:SS or seconds)")
@click.option("--end", help="End timestamp (HH:MM:SS)")
@click.option("--duration", type=float, help="Duration in seconds (alternative to --end)")
@click.option("--name", help="Output filename stem (default: source_id + start)")
@click.option("--fast", is_flag=True, help="Stream-copy instead of re-encode (faster but keyframe-aligned)")
@click.option("--audio-only", "audio_only", is_flag=True, help="Extract audio only (for dialogue overlays)")
def sources_extract(
    project: str,
    source_id: str,
    start: str,
    end: str | None,
    duration: float | None,
    name: str | None,
    fast: bool,
    audio_only: bool,
) -> None:
    """Extract a time range from a downloaded source.

    Saves video to projects/<slug>/selects/ by default, or audio to
    projects/<slug>/dialogue/ when --audio-only is set.
    """
    raw_dir = Path("projects") / project / "raw"
    if audio_only:
        out_dir = Path("projects") / project / "dialogue"
        default_ext = "wav"
    else:
        out_dir = Path("projects") / project / "selects"
        default_ext = "mp4"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Find the downloaded file for this source
    candidates = list(raw_dir.glob(f"{source_id}.*"))
    video_files = [p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not video_files:
        console.print(
            f"[red]No downloaded video for[/red] {source_id}. Run: ff sources download --project {project} {source_id}"
        )
        sys.exit(1)
    source_file = video_files[0]

    stem = name or f"{source_id}_{start.replace(':', '-')}"
    out_path = out_dir / f"{stem}.{default_ext}"

    label = "audio" if audio_only else "clip"
    console.print(f"[cyan]Extracting {label}[/cyan] {source_file.name} [{start} → {end or f'+{duration}s'}]")
    result = extract_range(
        source_file,
        out_path,
        start=start,
        end=end,
        duration=duration,
        reencode=not fast,
        audio_only=audio_only,
    )
    if result.success:
        console.print(f"  [green]✓[/green] {result.path}")
    else:
        console.print(f"  [red]✗[/red] {result.stderr.splitlines()[-1] if result.stderr else 'failed'}")
        sys.exit(1)


@sources.command("transcripts")
@click.option("--project", required=True)
@click.option("--all", "transcripts_all", is_flag=True, help="Fetch for all sources")
@click.option("--priority", help="Fetch for all sources with this priority")
@click.argument("source_ids", nargs=-1)
def sources_transcripts(
    project: str,
    transcripts_all: bool,
    priority: str | None,
    source_ids: tuple[str, ...],
) -> None:
    """Fetch transcripts for one or more sources (no video download)."""
    cat = SourceCatalog(_source_catalog_path(project))

    if transcripts_all:
        targets = [s for s in cat.all() if s.url.startswith("http")]
    elif priority:
        targets = [s for s in cat.by_priority(priority) if s.url.startswith("http")]
    else:
        targets = [cat.get(sid) for sid in source_ids if cat.get(sid) is not None]
        targets = [t for t in targets if t and t.url.startswith("http")]

    if not targets:
        console.print("[yellow]No sources to fetch.[/yellow]")
        sys.exit(1)

    out_dir = Path("projects") / project / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    for src in targets:
        console.print(f"[cyan]Fetching transcript[/cyan] {src.id}")
        result = fetch_transcript(src.url, out_dir, filename_base=src.id)
        if result.success:
            console.print(f"  [green]✓[/green] {result.srt_path}")
            # Show first 120 chars of plain text as preview
            preview = result.plain_text[:120].replace("\n", " ")
            console.print(f"  [dim]{preview}…[/dim]")
        else:
            console.print(f"  [red]✗[/red] {result.stderr.splitlines()[-1] if result.stderr else 'failed'}")


@sources.command("add")
@click.option("--project", required=True)
@click.option("--id", "source_id", required=True)
@click.option("--game", required=True)
@click.option("--title", required=True)
@click.option("--url", required=True)
@click.option("--duration", default="")
@click.option("--channel", default="")
@click.option("--priority", default="secondary")
@click.option("--contains", multiple=True, help="Characters/tags in the source (repeatable)")
@click.option("--notes", default="")
def sources_add(
    project: str,
    source_id: str,
    game: str,
    title: str,
    url: str,
    duration: str,
    channel: str,
    priority: str,
    contains: tuple[str, ...],
    notes: str,
) -> None:
    """Manually add a source to the project catalog."""
    cat = SourceCatalog(_source_catalog_path(project))
    src = Source(
        id=source_id,
        game=game,
        title=title,
        url=url,
        duration=duration,
        channel=channel,
        priority=priority,
        contains=list(contains),
        notes=notes,
    )
    cat.add(src)
    console.print(f"[green]Added[/green] {source_id} to {project}")


# ---------- assembly / rough cut ----------

@main.group()
def shots() -> None:
    """Shot list parsing and preview."""


@shots.command("parse")
@click.option("--project", required=True)
@click.option("--file", "shot_file", default="shot-list.md", help="Shot list filename within project")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write JSON to this path")
def shots_parse(project: str, shot_file: str, output: Path | None) -> None:
    """Parse a shot-list.md into JSON. Checks root, plans/, and demos/."""
    proj = Path("projects") / project
    candidates = [proj / shot_file, proj / "plans" / shot_file, proj / "demos" / shot_file]
    shot_path = next((p for p in candidates if p.exists()), None)
    if shot_path is None:
        console.print(
            f"[red]Not found:[/red] looked in {proj}/{{.,plans,demos}}/{shot_file}"
        )
        sys.exit(1)

    shot_entries = parse_shot_list(shot_path)
    data = shots_to_dict(shot_entries)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2))
        console.print(f"[green]Wrote[/green] {output} — {len(shot_entries)} shots")
    else:
        console.print(f"[cyan]{len(shot_entries)} shots parsed[/cyan]")
        table = Table(title="First 20 shots")
        table.add_column("#")
        table.add_column("Act")
        table.add_column("Time")
        table.add_column("Dur")
        table.add_column("Source")
        table.add_column("TS")
        table.add_column("Hero")
        for s in shot_entries[:20]:
            table.add_row(
                str(s.number),
                str(s.act),
                f"{s.song_time_sec:.1f}",
                f"{s.duration_sec:.1f}",
                s.source_id[:20] if s.source_id else "—",
                s.source_timestamp[:12] if s.source_timestamp else "—",
                s.hero,
            )
        console.print(table)


@main.command("assemble")
@click.option("--project", required=True)
@click.option("--file", "shot_file", default="shot-list.md")
@click.option("--output", default="assembled.mp4", help="Output filename in exports/")
@click.option("--width", default=1920, type=int)
@click.option("--height", default=1080, type=int)
@click.option("--fps", default=24, type=int)
def assemble_cmd(
    project: str,
    shot_file: str,
    output: str,
    width: int,
    height: int,
    fps: int,
) -> None:
    """Assemble a video from the shot list (no audio mix yet)."""
    proj = Path("projects") / project
    exports = proj / "exports"
    exports.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Parsing[/cyan] shot list...")
    shots_list = parse_shot_list(proj / shot_file)
    console.print(f"  {len(shots_list)} shots")

    console.print(f"[cyan]Assembling[/cyan] clips...")
    from fandomforge.assembly import assemble_rough_cut

    result = assemble_rough_cut(
        shots=shots_list,
        raw_dir=proj / "raw",
        output_path=exports / output,
        width=width,
        height=height,
        fps=fps,
        on_progress=lambda i, total, num: None,
    )

    if result.success:
        console.print(f"[green]✓[/green] {result.output_path}")
        console.print(f"  Assembled: {result.clips_assembled}, Skipped: {result.clips_skipped}")
        console.print(f"  Duration: {result.duration_sec:.1f}s")
        for w in result.warnings[:10]:
            console.print(f"  [yellow]warn:[/yellow] {w}")
        for r in result.skipped_reasons[:5]:
            console.print(f"  [red]skip:[/red] {r}")
    else:
        console.print(f"[red]✗[/red] {result.stderr}")
        for r in result.skipped_reasons[:10]:
            console.print(f"  {r}")
        sys.exit(1)


@main.command("color")
@click.option("--project", required=True)
@click.option("--input", "input_name", required=True, help="Input video in exports/")
@click.option("--output", default=None)
@click.option(
    "--preset",
    type=click.Choice([p.value for p in ColorPreset]),
    default=ColorPreset.TACTICAL.value,
)
@click.option("--lut", type=click.Path(path_type=Path), help="Path to .cube LUT file")
@click.option("--lut-intensity", type=float, default=0.75)
def color_cmd(
    project: str,
    input_name: str,
    output: str | None,
    preset: str,
    lut: Path | None,
    lut_intensity: float,
) -> None:
    """Apply a color grade to an assembled video."""
    proj = Path("projects") / project
    exports = proj / "exports"
    in_path = exports / input_name
    out_path = exports / (output or in_path.stem + "_graded.mp4")

    preset_enum = ColorPreset(preset)
    console.print(f"[cyan]Grading[/cyan] {in_path.name} → {out_path.name} (preset: {preset})")
    result = apply_base_grade(
        in_path,
        out_path,
        preset=preset_enum,
        lut_path=lut,
        lut_intensity=lut_intensity,
    )
    if result.success:
        console.print(f"[green]✓[/green] {result.output_path}")
    else:
        console.print(f"[red]✗[/red] {result.stderr}")
        sys.exit(1)


@main.command("scenedetect")
@click.option("--project", required=True)
@click.option("--source", "source_id", required=True)
@click.option("--threshold", type=float, default=27.0)
@click.option("--min-scene", type=float, default=1.0)
@click.option("-o", "--output", type=click.Path(path_type=Path))
def scenedetect_cmd(
    project: str,
    source_id: str,
    threshold: float,
    min_scene: float,
    output: Path | None,
) -> None:
    """Detect scenes in a downloaded source video."""
    raw = Path("projects") / project / "raw"
    candidates = list(raw.glob(f"{source_id}.*"))
    video_files = [p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not video_files:
        console.print(f"[red]No downloaded video for {source_id}[/red]")
        sys.exit(1)

    console.print(f"[cyan]Detecting scenes in[/cyan] {video_files[0].name}")
    scenes = detect_scenes(video_files[0], threshold=threshold, min_scene_sec=min_scene)
    console.print(f"  {len(scenes)} scenes detected")

    data = {
        "source_id": source_id,
        "source_file": str(video_files[0]),
        "threshold": threshold,
        "min_scene_sec": min_scene,
        "scenes": [
            {"index": i, "start": s.start_sec, "end": s.end_sec, "duration": s.duration_sec}
            for i, s in enumerate(scenes)
        ],
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2))
        console.print(f"[green]Wrote[/green] {output}")
    else:
        table = Table(title=f"Scenes — {source_id}")
        table.add_column("#", justify="right")
        table.add_column("Start")
        table.add_column("End")
        table.add_column("Duration", justify="right")
        for i, s in enumerate(scenes[:30]):
            table.add_row(
                str(i), f"{s.start_sec:.2f}", f"{s.end_sec:.2f}", f"{s.duration_sec:.2f}"
            )
        console.print(table)
        if len(scenes) > 30:
            console.print(f"[dim]...and {len(scenes) - 30} more[/dim]")


@main.command("trim")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output", "output_path", required=True, type=click.Path(path_type=Path))
@click.option("--threshold", default=0.04, type=float)
@click.option("--margin", default=0.1, type=float)
def trim_cmd(input_path: Path, output_path: Path, threshold: float, margin: float) -> None:
    """Trim silence from an audio or video file using auto-editor."""
    console.print(f"[cyan]Trimming silence[/cyan] from {input_path.name}")
    result = trim_silence(input_path, output_path, silence_threshold=threshold, margin_sec=margin)
    if result.success:
        console.print(f"[green]✓[/green] {result.output_path}")
    else:
        console.print(f"[red]✗[/red] {result.stderr}")
        sys.exit(1)


# ---------- dialogue ----------

@main.group()
def dialogue() -> None:
    """Dialogue script parsing and JSON compilation."""


@dialogue.command("parse")
@click.option("--project", required=True)
@click.option("--file", "dialogue_file", default="dialogue-script.md")
@click.option("-o", "--output", type=click.Path(path_type=Path))
def dialogue_parse(project: str, dialogue_file: str, output: Path | None) -> None:
    """Parse a dialogue-script.md into JSON cues. Checks root and plans/."""
    proj = Path("projects") / project
    candidates = [proj / dialogue_file, proj / "plans" / dialogue_file]
    md_path = next((p for p in candidates if p.exists()), None)
    if md_path is None:
        console.print(f"[red]Not found in[/red] {proj}/{{.,plans}}/{dialogue_file}")
        sys.exit(1)

    entries = parse_dialogue_script(md_path)
    data = entries_to_json(entries)

    if output is None:
        data_dir = proj / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        output = data_dir / "dialogue-script.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2))
    console.print(f"[green]Wrote[/green] {output} — {len(entries)} cues")

    table = Table(title="First 12 dialogue cues")
    table.add_column("Start", justify="right")
    table.add_column("Dur", justify="right")
    table.add_column("Char")
    table.add_column("Line")
    table.add_column("Audio filename")
    for e in entries[:12]:
        table.add_row(
            f"{e.start_sec:.1f}",
            f"{e.duration_sec:.1f}",
            e.character,
            e.line[:40] + ("…" if len(e.line) > 40 else ""),
            e.audio_filename,
        )
    console.print(table)


@dialogue.command("show")
@click.option("--project", required=True)
@click.option("--file", "dialogue_file", default="dialogue-script.json")
def dialogue_show(project: str, dialogue_file: str) -> None:
    """Show the dialogue cues JSON. Checks data/ (new) and root (legacy)."""
    proj = Path("projects") / project
    candidates = [proj / "data" / dialogue_file, proj / dialogue_file]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        console.print(f"[red]Not found in[/red] {proj}/data/{dialogue_file} or {proj}/{dialogue_file}")
        sys.exit(1)
    entries = load_dialogue_json(path)
    console.print(f"[cyan]{len(entries)} cues[/cyan] in {path}")
    table = Table(title=f"Dialogue cues — {project}")
    table.add_column("#", justify="right")
    table.add_column("Start", justify="right")
    table.add_column("Dur", justify="right")
    table.add_column("Gain", justify="right")
    table.add_column("Char")
    table.add_column("Audio file")
    for i, e in enumerate(entries, start=1):
        table.add_row(
            str(i),
            f"{e.start_sec:.1f}",
            f"{e.duration_sec:.1f}",
            f"{e.gain_db:.0f}",
            e.character,
            e.audio_filename,
        )
    console.print(table)


# ---------- color plan ----------

@main.group("color-plan")
def color_plan_grp() -> None:
    """Per-source color plan management."""


@color_plan_grp.command("init")
@click.option("--project", required=True)
@click.option(
    "--default",
    "default_preset",
    type=click.Choice([p.value for p in ColorPreset]),
    default=ColorPreset.TACTICAL.value,
)
def color_plan_init(project: str, default_preset: str) -> None:
    """Create a starter color-plan.json from the sources catalog with the default preset."""
    proj = Path("projects") / project
    cat = SourceCatalog(_source_catalog_path(project))
    if not cat.all():
        console.print(f"[yellow]No sources in catalog for {project}[/yellow]")
    plan = ColorPlan(default=ColorPreset(default_preset))
    for src in cat.all():
        plan.sources[src.id] = ColorPreset(default_preset)
    data_dir = proj / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    plan_path = data_dir / "color-plan.json"
    save_color_plan(plan, plan_path)
    console.print(f"[green]Wrote[/green] {plan_path}")
    console.print(f"  Default: {default_preset}")
    console.print(f"  Sources: {len(plan.sources)} (all set to default — edit to override per-source)")


@color_plan_grp.command("show")
@click.option("--project", required=True)
def color_plan_show(project: str) -> None:
    """Display the color plan for a project. Checks data/ (new) and root (legacy)."""
    proj = Path("projects") / project
    candidates = [proj / "data" / "color-plan.json", proj / "color-plan.json"]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        console.print(f"[red]No color-plan.json found[/red] in {proj}/data/ or {proj}/")
        console.print(f"  Run: ff color-plan init --project {project}")
        sys.exit(1)
    plan = load_color_plan(path)
    console.print(f"[cyan]Color plan for {project}[/cyan]")
    console.print(f"  Default: [bold]{plan.default.value}[/bold]")
    if plan.act_overrides:
        console.print("  Act overrides:")
        for act, preset in sorted(plan.act_overrides.items()):
            console.print(f"    Act {act}: {preset.value}")
    if plan.sources:
        table = Table(title="Per-source presets")
        table.add_column("Source ID")
        table.add_column("Preset")
        for src_id, preset in sorted(plan.sources.items()):
            table.add_row(src_id, preset.value)
        console.print(table)


# ---------- scripts generation ----------

@main.group()
def scripts() -> None:
    """Generate helper scripts (downloads, dialogue extraction)."""


@scripts.command("download")
@click.option("--project", required=True)
@click.option("--priority", default="primary")
@click.option("--resolution", default="1080")
def scripts_download(project: str, priority: str, resolution: str) -> None:
    """Generate a download-all.sh script for the project."""
    proj = Path("projects") / project
    cat = SourceCatalog(_source_catalog_path(project))
    if not cat.all():
        console.print(f"[red]No sources catalog at[/red] {_source_catalog_path(project)}")
        sys.exit(1)
    out_path = proj / "scripts" / "download-all.sh"
    count = generate_download_script(
        project, cat, out_path, priority=priority, resolution=resolution
    )
    console.print(f"[green]Wrote[/green] {out_path}")
    console.print(f"  {count} sources (priority={priority}, resolution={resolution}p)")
    console.print(f"  Run: [cyan]{out_path}[/cyan]")


@scripts.command("dialogue")
@click.option("--project", required=True)
@click.option("--file", "dialogue_file", default="dialogue-script.md")
@click.option(
    "--hint",
    multiple=True,
    help='Map script source-citation to catalog source-id, e.g. "RE6=re6-leon-edition". Repeatable.',
)
@click.option(
    "--character",
    multiple=True,
    help='Character fallback, e.g. "Leon=re6-leon-edition". Used when cue has no citation. Repeatable.',
)
def scripts_dialogue(
    project: str,
    dialogue_file: str,
    hint: tuple[str, ...],
    character: tuple[str, ...],
) -> None:
    """Generate an extract-dialogue.sh script for the project."""
    proj = Path("projects") / project
    md_path = proj / dialogue_file
    if not md_path.exists():
        console.print(f"[red]Not found:[/red] {md_path}")
        sys.exit(1)

    hints: dict[str, str] = {}
    for h in hint:
        if "=" in h:
            k, v = h.split("=", 1)
            hints[k.strip()] = v.strip()

    char_hints: dict[str, str] = {}
    for c in character:
        if "=" in c:
            k, v = c.split("=", 1)
            char_hints[k.strip()] = v.strip()

    entries = parse_dialogue_script(md_path)
    out_path = proj / "scripts" / "extract-dialogue.sh"
    count = generate_extract_dialogue_script(
        project, entries, hints, out_path, character_hints=char_hints
    )
    console.print(f"[green]Wrote[/green] {out_path}")
    console.print(f"  {count} dialogue cues — edit the generated script to set exact timestamps")
    if char_hints:
        console.print(f"  Character fallbacks applied: {', '.join(char_hints.keys())}")


@main.command("roughcut")
@click.option("--project", required=True)
@click.option("--shot-list", "shot_list_name", default="shot-list.md")
@click.option("--song", "song_filename", help="Song file in raw/ (optional)")
@click.option("--dialogue", "dialogue_script", help="Dialogue script JSON file (in project dir)")
@click.option("--color-plan", "color_plan_file", help="color-plan.json (in project dir); overrides --color")
@click.option(
    "--color",
    "color_preset",
    type=click.Choice([p.value for p in ColorPreset]),
    default=ColorPreset.TACTICAL.value,
)
@click.option("--output", default="rough-cut.mp4")
@click.option("--width", default=1920, type=int)
@click.option("--height", default=1080, type=int)
@click.option("--fps", default=24, type=int)
@click.option("--song-offset", type=float, default=0.0, help="Start offset into song (seconds)")
def roughcut_cmd(
    project: str,
    shot_list_name: str,
    song_filename: str | None,
    dialogue_script: str | None,
    color_plan_file: str | None,
    color_preset: str,
    output: str,
    width: int,
    height: int,
    fps: int,
    song_offset: float,
) -> None:
    """Build a complete rough-cut video: parse → assemble → color → mix → mux."""
    proj = Path("projects") / project

    # Pass filenames through to orchestrator — it does the path resolution
    # across root, plans/, data/, and demos/ on its own.
    dialogue_json_path: str | None = dialogue_script or None
    color_plan_path: str | None = color_plan_file or None

    console.print(f"[cyan]Building rough cut[/cyan] for {project}...")
    console.print(f"  shot list: {shot_list_name}")
    console.print(f"  song: {song_filename or 'none (silent track)'}")
    console.print(f"  dialogue: {dialogue_json_path or 'none'}")
    console.print(f"  color: {'(per-source plan)' if color_plan_path else color_preset}")

    result = build_rough_cut(
        project_dir=proj,
        shot_list_name=shot_list_name,
        song_filename=song_filename,
        dialogue_script_json=dialogue_json_path,
        color_preset=ColorPreset(color_preset),
        color_plan_json=color_plan_path,
        output_name=output,
        target_width=width,
        target_height=height,
        target_fps=fps,
        song_start_offset_sec=song_offset,
    )

    if result.success:
        console.print(f"[green]✓ Rough cut complete[/green]")
        console.print(f"  Output: [bold]{result.output_path}[/bold]")
        console.print(f"  Duration: {result.duration_sec:.1f}s")
        if result.assembly:
            console.print(
                f"  Clips: {result.assembly.clips_assembled} assembled, "
                f"{result.assembly.clips_skipped} skipped"
            )
        if result.mix:
            console.print(f"  Dialogue cues mixed: {result.mix.dialogue_count}")
        for w in result.warnings[:10]:
            console.print(f"  [yellow]warn:[/yellow] {w}")

        # Auto-test the output: check black frames, loudness, dialogue intelligibility
        if dialogue_json_path and result.output_path:
            # Orchestrator did path resolution; locate the file by searching
            # project root + common subdirs (demos/, plans/, data/).
            dialogue_full = None
            for base in (proj, proj / "demos", proj / "plans", proj / "data"):
                candidate = base / dialogue_json_path
                if candidate.exists():
                    dialogue_full = candidate
                    break
            if dialogue_full is not None and dialogue_full.exists():
                console.print(f"\n[cyan]Auto-testing rendered cut...[/cyan]")
                from fandomforge.intelligence.auto_test import (
                    run_auto_test, print_report,
                )
                report = run_auto_test(
                    video_path=result.output_path,
                    dialogue_cues_json=dialogue_full,
                    use_whisper=True,
                )
                print_report(report)
                report_path = result.output_path.with_suffix(".auto-test.json")
                report_path.write_text(json.dumps(report.to_dict(), indent=2))
                console.print(f"  Report: {report_path}")
    else:
        console.print(f"[red]✗[/red] {result.stderr}")
        sys.exit(1)


# ---------- find (SRT-powered dialogue search) ----------

@main.group()
def find() -> None:
    """Find dialogue lines / shots in downloaded sources."""


@find.command("line")
@click.option("--project", required=True)
@click.option("--query", required=True, help="The dialogue line you're looking for (any keywords).")
@click.option("--top", type=int, default=5, help="Top K matches")
@click.option("--min-score", type=float, default=0.4)
def find_line_cmd(project: str, query: str, top: int, min_score: float) -> None:
    """Search all SRT transcripts in a project for a dialogue line."""
    from fandomforge.intelligence.dialogue_finder import find_dialogue_across_project
    proj = Path("projects") / project
    matches = find_dialogue_across_project(proj, query, top_k=top, min_score=min_score)
    if not matches:
        console.print(f"[yellow]No matches found[/yellow] for: {query!r}")
        console.print(f"  SRTs searched: {proj}/transcripts/*.srt and {proj}/raw/*.srt")
        return
    table = Table(title=f"Dialogue matches — {query[:50]}")
    table.add_column("Score", justify="right")
    table.add_column("Source")
    table.add_column("Time", justify="right")
    table.add_column("Text")
    for m in matches:
        table.add_row(
            f"{m.score:.2f}",
            m.source_id[:25],
            f"{m.start_sec:.1f}s",
            m.text[:80] + ("…" if len(m.text) > 80 else ""),
        )
    console.print(table)


@find.command("shot")
@click.option("--project", required=True)
@click.option("--query", required=True, help="Natural-language shot description")
@click.option("--top", type=int, default=10)
def find_shot_cmd(project: str, query: str, top: int) -> None:
    """Search CLIP-indexed frames for shots matching the description. Requires ff index-frames first."""
    proj = Path("projects") / project
    cache = proj / ".clip-cache"
    if not cache.exists():
        console.print("[yellow]No CLIP index built yet.[/yellow]")
        console.print(f"  Run: ff index-frames --project {project}")
        return
    from fandomforge.intelligence.clip_search import semantic_search
    results = semantic_search(cache, query, top_k=top)
    if not results:
        console.print("[yellow]No matches.[/yellow]")
        return
    table = Table(title=f"CLIP matches — {query[:50]}")
    table.add_column("Score", justify="right")
    table.add_column("Source")
    table.add_column("Time", justify="right")
    for r in results:
        table.add_row(f"{r.score:.3f}", r.source_id[:30], f"{r.time_sec:.1f}s")
    console.print(table)


@main.command("index-frames")
@click.option("--project", required=True)
@click.option("--source", "source_ids", multiple=True, help="Limit to specific source IDs (repeatable)")
@click.option("--interval", type=float, default=5.0, help="Seconds between sampled frames")
def index_frames_cmd(project: str, source_ids: tuple[str, ...], interval: float) -> None:
    """Build CLIP embeddings index for source videos. Enables ff find shot."""
    from fandomforge.intelligence.clip_search import build_frame_index, _have_clip
    if not _have_clip():
        console.print("[red]CLIP not installed.[/red] Run: pip install open_clip_torch torch")
        sys.exit(1)
    proj = Path("projects") / project
    raw = proj / "raw"
    cache = proj / ".clip-cache"
    cache.mkdir(parents=True, exist_ok=True)

    videos = sorted(raw.glob("*.mp4")) + sorted(raw.glob("*.mkv")) + sorted(raw.glob("*.webm"))
    if source_ids:
        videos = [v for v in videos if v.stem in source_ids]
    if not videos:
        console.print(f"[yellow]No videos to index in[/yellow] {raw}")
        return
    for v in videos:
        console.print(f"[cyan]Indexing[/cyan] {v.name}")
        path = build_frame_index(v, cache, source_id=v.stem, interval_sec=interval)
        if path:
            console.print(f"  [green]✓[/green] {path.name}")
        else:
            console.print(f"  [red]✗[/red]")


# ---------- visual-quality (per-shot HUD/watermark/artifact scoring) ----------

@main.command("visual-quality")
@click.option("--project", required=True)
@click.option("--force", is_flag=True, help="Rescore shots that already have scores")
@click.option("--limit", type=int, default=None, help="Max shots to score this run")
@click.option("--parallel", type=int, default=8, help="Concurrent API calls")
@click.option("--demote-threshold", type=int, default=60,
              help="Shots scoring below this get pushed to the back of the broll queue")
def visual_quality_cmd(
    project: str, force: bool, limit: int | None,
    parallel: int, demote_threshold: int,
) -> None:
    """Per-shot HUD/watermark/artifact scoring via GPT-4o-mini vision.

    Stamps a visual_quality score (0-100) on every shot in the library so
    the broll picker can skip shots that have gameplay HUD or watermarks
    burned in — even when the original caption didn't mention them.

    One-time pass per project. Cost ~$0.003/shot (≈$9 for 3000 shots).
    """
    from fandomforge.intelligence.visual_quality import score_library, drop_low_quality_shots

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        proj_dir = Path(project)
    db_path = proj_dir / ".shot-library.db"
    raw_dir = proj_dir / "raw"
    if not db_path.exists():
        console.print(f"[red]No shot library at {db_path}[/red]")
        sys.exit(1)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        console.print("[red]OPENAI_API_KEY required[/red]")
        sys.exit(1)

    scored = score_library(
        db_path, raw_dir, api_key,
        force=force, limit=limit, parallel=parallel,
    )
    console.print(f"\nScored {scored} shots")
    demoted = drop_low_quality_shots(db_path, quality_threshold=demote_threshold)
    console.print(f"Demoted {demoted} shots below quality {demote_threshold}")


# ---------- verify (render proofing) ----------

@main.command("verify")
@click.option("--project", required=True)
@click.option("--video", default="final.mp4",
              help="Which export to verify (exports/<video>)")
@click.option("--plan", default=".layered-plan-final.json",
              help="Plan file to verify against")
@click.option("--no-vision", is_flag=True,
              help="Skip GPT-4o frame checks (faster, only audio+pacing)")
def verify_cmd(project: str, video: str, plan: str, no_vision: bool) -> None:
    """Proof a rendered edit against its plan.

    Extracts frames at sync-anchor + broll-sample timestamps, sends them
    to GPT-4o vision, measures per-cue voice-lift, checks pacing variance
    and beat alignment. Reports every issue with specific timestamps.

    Use after every make-edit run to catch renders that technically
    pass QA but don't actually look or sound right.
    """
    from fandomforge.intelligence.render_verifier import verify_render

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        proj_dir = Path(project)
    video_path = proj_dir / "exports" / video
    plan_path = proj_dir / plan
    if not video_path.exists():
        console.print(f"[red]No video at {video_path}[/red]")
        sys.exit(1)
    if not plan_path.exists():
        console.print(f"[red]No plan at {plan_path}[/red]")
        sys.exit(1)

    api_key = None if no_vision else os.environ.get("OPENAI_API_KEY")

    console.print(f"[cyan]Verifying[/cyan] {video_path}")
    report = verify_render(
        video_path, plan_path, proj_dir,
        api_key=api_key,
    )
    console.print()
    console.print(report.summary())
    console.print()
    out_json = proj_dir / "exports" / f"{video_path.stem}.verify-report.json"
    console.print(f"[dim]Full report: {out_json}[/dim]")
    sys.exit(0 if report.passed else 1)


# ---------- audio-synth (AudioCraft SFX generation) ----------

@main.command("audio-synth")
@click.option("--project", required=True)
@click.option("--presets", default=None,
              help="Comma-separated preset names (omit for full pack)")
@click.option("--sr", type=int, default=48000, help="Output sample rate")
def audio_synth_cmd(project: str, presets: str | None, sr: int) -> None:
    """Generate a pack of cinematic SFX into projects/<slug>/sfx/.

    Requires audiocraft (`pip install audiocraft`). Downloads ~4 GB of
    model weights on first run.
    """
    from fandomforge.intelligence import audio_synth

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        proj_dir = Path(project)
    if not proj_dir.exists():
        console.print(f"[red]No project at {proj_dir}[/red]")
        sys.exit(1)

    if not audio_synth.is_available():
        console.print("[yellow]⚠ audiocraft not installed.[/yellow]")
        for k, v in audio_synth.availability_report().items():
            console.print(f"    {k}: {v}")
        console.print("[dim]Install: pip install audiocraft[/dim]")
        sys.exit(1)

    preset_list = presets.split(",") if presets else None
    console.print(f"[cyan]Synthesizing SFX pack[/cyan] to {proj_dir}/sfx/")
    results = audio_synth.synthesize_sfx_pack(
        proj_dir / "sfx", presets=preset_list, sample_rate_hz=sr,
    )
    ok_count = sum(1 for r in results if r.ok)
    for r in results:
        mark = "[green]✓[/green]" if r.ok else "[red]✗[/red]"
        console.print(f"  {mark} {r.prompt[:50]}  ({r.duration_sec:.1f}s)")
        if not r.ok:
            console.print(f"      [dim]{r.reason}[/dim]")
    console.print(f"\n{ok_count}/{len(results)} generated")


# ---------- agent (natural-language directives) ----------

@main.command("agent")
@click.option("--project", required=True)
@click.option("--dry-run", is_flag=True, help="Show what would change without saving")
@click.option("--no-llm", is_flag=True, help="Skip LLM intent parsing (rules only)")
@click.argument("prompt", nargs=-1, required=True)
def agent_cmd(project: str, dry_run: bool, no_llm: bool, prompt: tuple[str, ...]) -> None:
    """Apply a natural-language directive to a project without re-rendering.

    Examples:
        ff agent --project dean-winchester-renegades make it sadder
        ff agent --project leon-badass-monologue cut 15 seconds and more Ada
        ff agent --project dean-winchester-renegades lean on the brother arc

    The agent updates project-config.yaml in place; run ff make-edit to
    re-render with the adjusted knobs.
    """
    from fandomforge.intelligence.agentic import Agent, AgentContext, save_intent_log

    full_prompt = " ".join(prompt).strip()
    if not full_prompt:
        console.print("[red]Empty prompt[/red]")
        sys.exit(1)

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        proj_dir = Path(project)
    if not (proj_dir / "project-config.yaml").exists():
        console.print(f"[red]No project-config.yaml at {proj_dir}[/red]")
        sys.exit(1)

    ctx = AgentContext.from_project(proj_dir)
    agent = Agent(ctx, prefer_llm=not no_llm)
    result = agent.run(full_prompt, commit=not dry_run)

    console.print(f"\n[cyan]Prompt:[/cyan] {full_prompt}")
    console.print(f"[cyan]Parsed intent:[/cyan]")
    intent = result.intent
    if intent.mood_shift:
        console.print(f"  mood_shift: {intent.mood_shift}")
    if intent.character_emphasis:
        console.print(f"  emphasize: {intent.character_emphasis}")
    if intent.character_deemphasis:
        console.print(f"  deemphasize: {intent.character_deemphasis}")
    if intent.narrative_boost:
        console.print(f"  boost arcs: {intent.narrative_boost}")
    if intent.pacing:
        console.print(f"  pacing: {intent.pacing}")
    if intent.duration_delta_sec is not None:
        console.print(f"  duration delta: {intent.duration_delta_sec:+.0f}s")
    if intent.lut_nudge is not None:
        console.print(f"  LUT nudge: {intent.lut_nudge:+.2f}")

    console.print(f"\n[cyan]Actions:[/cyan]")
    for a in result.actions:
        console.print(f"  [green]•[/green] {a}")
    if result.warnings:
        console.print(f"\n[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")

    if dry_run:
        console.print(f"\n[dim]--dry-run: config not saved[/dim]")
    elif result.committed:
        console.print(f"\n[green]✓ Saved to {proj_dir}/project-config.yaml[/green]")
        save_intent_log(proj_dir, full_prompt, result)
        console.print(f"[dim]Logged to {proj_dir}/.agent-log.jsonl[/dim]")
        console.print(f"\nRe-render with: [cyan]ff make-edit --project {project}[/cyan]")


# ---------- vo extract (smart VO extraction) ----------

@main.command("vo-extract")
@click.option("--project", required=True)
@click.option("--max-lines", type=int, default=12, help="Max lines per source")
@click.option("--no-verify", is_flag=True, help="Skip Whisper verification")
@click.option("--isolate-voice", is_flag=True,
              help="Run AudioSep voice isolation on each clip (requires AudioSep install)")
@click.option("--voice-query", default=None,
              help="AudioSep natural-language query (e.g. 'a gruff male voice')")
def vo_extract_cmd(
    project: str, max_lines: int, no_verify: bool,
    isolate_voice: bool, voice_query: str | None,
) -> None:
    """Extract clean VO wavs via ASR-driven filler/repetition removal.

    Reads project-config.yaml for character + era_source_map +
    narrative_priorities, parses every raw/*.en.vtt, cleans fillers and
    repetitions, picks top-N lines per source by score, cuts wavs, and
    writes dialogue/*.wav + transcript-map.json + source-map.json.

    Replaces the old keyword-only VTT picker with proper speech rough cut.
    """
    from fandomforge.config import load_project_config
    from fandomforge.intelligence.vo_extractor import extract_vo_library

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        proj_dir = Path(project)  # allow absolute path
    if not (proj_dir / "project-config.yaml").exists() and \
       not (proj_dir / "project-config.json").exists():
        console.print(f"[red]No project-config.yaml at {proj_dir}[/red]")
        sys.exit(1)

    cfg = load_project_config(proj_dir)
    console.print(f"[cyan]Extracting VO[/cyan] for character={cfg.character}")
    console.print(f"  sources: {list(cfg.era_source_map.values())}")
    console.print(f"  priorities ({len(cfg.narrative_priorities)}): "
                  f"{cfg.narrative_priorities[:4]}{'...' if len(cfg.narrative_priorities) > 4 else ''}")

    if isolate_voice:
        from fandomforge.intelligence import voice_isolator
        report = voice_isolator.availability_report()
        if not voice_isolator.is_available():
            console.print(f"[yellow]⚠ --isolate-voice requested but AudioSep not found.[/yellow]")
            for k, v in report.items():
                console.print(f"    {k}: {v}")
            console.print(f"[dim]Continuing without isolation (pass-through).[/dim]")
        else:
            console.print(f"[green]✓[/green] AudioSep found at {report['repo_path']}")

    result = extract_vo_library(
        proj_dir,
        character=cfg.character,
        era_source_map=cfg.era_source_map,
        narrative_priorities=cfg.narrative_priorities,
        max_lines_per_source=max_lines,
        verify_with_whisper=not no_verify,
        isolate_voice=isolate_voice,
        voice_query=voice_query,
    )

    console.print(f"\n[green]Kept[/green] {len(result.kept)} lines")
    for seg in result.kept:
        console.print(
            f"  [{seg.source_stem}] {seg.duration_sec:.2f}s  "
            f"score={seg.score:.1f}  \"{seg.text[:70]}\""
        )
    if result.dropped:
        console.print(f"\n[yellow]Dropped[/yellow] {len(result.dropped)}")
        for seg, reason in result.dropped[:8]:
            console.print(f"  [{reason}]  \"{seg.text[:60]}\"")

    console.print(f"\n[cyan]Wrote[/cyan] {proj_dir}/dialogue/transcript-map.json")
    console.print(f"[cyan]Wrote[/cyan] {proj_dir}/dialogue/source-map.json")


# ---------- transcribe (Whisper API) ----------

@main.command("transcribe")
@click.option("--project", required=True)
@click.option("--source", "source_ids", multiple=True, help="Sources to transcribe (default: all without SRT)")
@click.option("--via", type=click.Choice(["openai", "local"]), default="openai")
@click.option("--model", default="whisper-1", help="openai: whisper-1; local: tiny/base/small/medium/large")
def transcribe_cmd(project: str, source_ids: tuple[str, ...], via: str, model: str) -> None:
    """Transcribe source videos → SRT files. Fills gaps for videos YouTube didn't caption."""
    from fandomforge.intelligence.openai_helper import transcribe_via_openai
    from fandomforge.intelligence.dialogue_finder import transcribe_with_whisper
    proj = Path("projects") / project
    raw = proj / "raw"
    transcripts = proj / "transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)

    videos = sorted(raw.glob("*.mp4")) + sorted(raw.glob("*.mkv"))
    if source_ids:
        videos = [v for v in videos if v.stem in source_ids]

    for v in videos:
        srt = transcripts / f"{v.stem}.en.srt"
        if srt.exists():
            console.print(f"[dim]skip {v.stem}[/dim] (already has SRT)")
            continue
        console.print(f"[cyan]Transcribing[/cyan] {v.name} via {via}")
        if via == "openai":
            result = transcribe_via_openai(v, srt, project_root=str(Path.cwd()))
            if result.success:
                console.print(f"  [green]✓[/green] {srt}")
            else:
                console.print(f"  [red]✗[/red] {result.error}")
        else:
            ok = transcribe_with_whisper(v, srt, model_size=model)
            console.print(f"  [{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/]")


# ---------- preview (thumbnail grid) ----------

@main.command("preview")
@click.option("--project", required=True)
@click.option("--shot-list", "shot_list_name", default="shot-list.md")
@click.option("--output", default="preview.png")
@click.option("--thumb-width", type=int, default=320)
@click.option("--cols", type=int, default=5)
def preview_cmd(project: str, shot_list_name: str, output: str, thumb_width: int, cols: int) -> None:
    """Generate a PNG contact sheet of every shot for visual QA."""
    from fandomforge.intelligence.preview import generate_contact_sheet
    proj = Path("projects") / project
    candidates = [proj / shot_list_name, proj / "plans" / shot_list_name, proj / "demos" / shot_list_name]
    shot_path = next((p for p in candidates if p.exists()), None)
    if shot_path is None:
        console.print(f"[red]Shot list not found[/red]")
        sys.exit(1)
    shots = parse_shot_list(shot_path)
    out_path = proj / "exports" / output
    console.print(f"[cyan]Generating contact sheet[/cyan] ({len(shots)} shots)")
    result = generate_contact_sheet(shots, proj / "raw", out_path, thumb_width=thumb_width, cols=cols)
    if result.success:
        console.print(f"  [green]✓[/green] {result.output_path}")
    else:
        console.print(f"  [red]✗[/red] {result.stderr}")
        sys.exit(1)


# ---------- LUT ----------

@main.group()
def lut() -> None:
    """LUT management (.cube files for cinematic color)."""


@lut.command("list")
def lut_list() -> None:
    """List all bundled + user-supplied LUTs."""
    from fandomforge.intelligence.lut import list_available_luts
    entries = list_available_luts("assets")
    table = Table(title=f"Available LUTs ({len(entries)})")
    table.add_column("Name")
    table.add_column("Description")
    for e in entries:
        table.add_row(e.name, e.description[:60])
    console.print(table)


@lut.command("apply")
@click.option("--project", required=True)
@click.option("--input", "input_video", required=True, help="Input video in exports/")
@click.option("--lut", "lut_name", required=True, help="LUT name (see ff lut list)")
@click.option("--output", required=True)
@click.option("--intensity", type=float, default=0.75)
def lut_apply_cmd(project: str, input_video: str, lut_name: str, output: str, intensity: float) -> None:
    """Apply a LUT to an exported video."""
    from fandomforge.intelligence.lut import apply_lut, list_available_luts
    proj = Path("projects") / project
    in_path = proj / "exports" / input_video
    out_path = proj / "exports" / output
    entries = list_available_luts("assets")
    lut_entry = next((e for e in entries if e.name == lut_name), None)
    if not lut_entry:
        console.print(f"[red]LUT not found:[/red] {lut_name}")
        console.print(f"  Available: {', '.join(e.name for e in entries)}")
        sys.exit(1)
    console.print(f"[cyan]Applying[/cyan] {lut_name} at {intensity*100:.0f}% → {out_path}")
    if apply_lut(in_path, out_path, lut_entry.path, intensity=intensity):
        console.print(f"  [green]✓[/green] {out_path}")
    else:
        console.print(f"  [red]✗[/red]")
        sys.exit(1)


# ---------- director (GPT edit suggestions) ----------

@main.command("director")
@click.option("--project", required=True)
@click.option("--shot-list", "shot_list_name", default="shot-list.md")
@click.option("--theme", help="Override theme (default: parse from edit-plan.md)")
@click.option("--target-runtime", type=float, default=60.0)
@click.option("--output", help="Write JSON suggestion to this filename (in project root)")
def director_cmd(
    project: str, shot_list_name: str, theme: str | None, target_runtime: float, output: str | None
) -> None:
    """Ask GPT Director for edit suggestions: act plan, transitions, problem shots, next action."""
    from fandomforge.intelligence.director import propose_edit
    proj = Path("projects") / project

    # Resolve shot list
    candidates = [proj / shot_list_name, proj / "plans" / shot_list_name, proj / "demos" / shot_list_name]
    shot_path = next((p for p in candidates if p.exists()), None)
    if shot_path is None:
        console.print(f"[red]Shot list not found[/red]")
        sys.exit(1)
    shots = parse_shot_list(shot_path)

    # Guess theme if not provided
    if not theme:
        for plan in [proj / "plans" / "edit-plan.md", proj / "edit-plan.md"]:
            if plan.exists():
                txt = plan.read_text()
                import re
                m = re.search(r"(?:THEME|Theme)\s*[\n|║].*?([^║\n]+)", txt)
                if m:
                    theme = m.group(1).strip().strip("*").strip()
                    break
    if not theme:
        theme = "(no theme — please pass --theme)"

    summaries = [
        {
            "number": s.number,
            "hero": s.hero,
            "description": s.description,
            "source_id": s.source_id,
            "duration_sec": s.duration_sec,
            "song_time_sec": s.song_time_sec,
        }
        for s in shots
    ]

    output_path = None
    if output:
        output_path = proj / output

    console.print(f"[cyan]Asking Director[/cyan] for edit suggestions on {len(shots)} shots...")
    console.print(f"  Theme: {theme}")
    result = propose_edit(
        theme=theme,
        shots_summary=summaries,
        target_runtime_sec=target_runtime,
        output_json=output_path,
    )
    if result.success:
        console.print(result.text)
        if result.json_path:
            console.print(f"\n[green]Wrote[/green] {result.json_path}")
    else:
        console.print(f"[red]✗[/red] {result.error}")
        sys.exit(1)


# ---------- TTS ----------

@main.command("tts")
@click.option("--project", required=True)
@click.option("--text", required=True, help="Text to synthesize")
@click.option("--output", required=True, help="Output filename (will save to dialogue/)")
@click.option("--voice", type=click.Choice(OPENAI_VOICES), default="onyx")
def tts_cmd(project: str, text: str, output: str, voice: str) -> None:
    """Generate speech audio from text via OpenAI TTS (or macOS say fallback)."""
    from fandomforge.intelligence.tts import synthesize_speech
    proj = Path("projects") / project
    out_dir = proj / "dialogue"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / output
    console.print(f"[cyan]Synthesizing[/cyan] {len(text)} chars via voice={voice}")
    result = synthesize_speech(text, out_path, voice=voice)
    if result.success:
        console.print(f"  [green]✓[/green] {result.output_path} (backend: {result.backend})")
    else:
        console.print(f"  [red]✗[/red] {result.error}")
        sys.exit(1)


# ---------- NLE export ----------

@main.command("export-nle")
@click.option("--project", required=True)
@click.option("--shot-list", "shot_list_name", default="shot-list.md")
@click.option(
    "--format", "fmt",
    type=click.Choice(["fcpxml", "edl", "both"]),
    default="both",
    help="fcpxml (Resolve/Premiere/FCP) or edl (legacy)",
)
@click.option("--audio-track", help="Optional mixed audio file to include in timeline")
@click.option("--fps", type=int, default=24)
@click.option("--width", type=int, default=1920)
@click.option("--height", type=int, default=1080)
@click.option("--output-base", default="timeline", help="Base filename (extension added)")
def export_nle_cmd(
    project: str, shot_list_name: str, fmt: str, audio_track: str | None,
    fps: int, width: int, height: int, output_base: str,
) -> None:
    """Export the shot list as a timeline for DaVinci Resolve / Premiere / FCP."""
    proj = Path("projects") / project
    candidates = [proj / shot_list_name, proj / "plans" / shot_list_name, proj / "demos" / shot_list_name]
    shot_path = next((p for p in candidates if p.exists()), None)
    if shot_path is None:
        console.print(f"[red]Shot list not found[/red]")
        sys.exit(1)

    shots = parse_shot_list(shot_path)
    clips = shots_to_clips(shots, proj / "raw")
    if not clips:
        console.print(f"[yellow]No resolvable clips[/yellow]")
        sys.exit(1)
    console.print(f"[cyan]Exporting[/cyan] {len(clips)} clips to NLE timeline")

    audio_path = None
    if audio_track:
        for c in [proj / "exports" / audio_track, proj / "raw" / audio_track, Path(audio_track)]:
            if c.exists():
                audio_path = c
                break

    out_dir = proj / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt in ("fcpxml", "both"):
        out = out_dir / f"{output_base}.fcpxml"
        export_fcpxml(
            clips, out, fps=fps, width=width, height=height,
            title=f"FandomForge — {project}",
            audio_track_path=audio_path,
        )
        console.print(f"  [green]✓[/green] {out}")
    if fmt in ("edl", "both"):
        out = out_dir / f"{output_base}.edl"
        export_edl(clips, out, fps=fps, title=f"FandomForge — {project}")
        console.print(f"  [green]✓[/green] {out}")

    console.print("")
    console.print(f"[bold]To open in DaVinci Resolve:[/bold]")
    console.print(f"  File → Import → Timeline → select {out_dir}/{output_base}.fcpxml")


# ---------- fix (feedback loop) ----------

@main.command("fix")
@click.option("--project", required=True, help="Project slug (e.g. leon-badass-monologue)")
@click.option("--plan", "plan_name", default=".edit-plan-v1.json", help="EditPlan JSON filename to load from project root")
@click.option("--shot", "shot_id", type=int, default=None, help="Cut index of the shot to fix")
@click.option("--cue", "cue_id", type=int, default=None, help="Dialogue cue index to fix")
@click.option("--pacing", "pacing_target", default=None, help="Act description to re-pace, e.g. 'act 2'")
@click.option("--color", "color_desc", default=None, help="Color issue description, e.g. 'too teal'")
@click.option("--reason", default="", help="Free-text reason for the correction")
@click.option("--from-file", "corrections_file", type=click.Path(path_type=Path), default=None, help="Load corrections from a JSON file instead of CLI flags")
@click.option("--out", "output_name", default=None, help="Output plan filename (default: auto-versioned)")
def fix_cmd(
    project: str,
    plan_name: str,
    shot_id: int | None,
    cue_id: int | None,
    pacing_target: str | None,
    color_desc: str | None,
    reason: str,
    corrections_file: Path | None,
    output_name: str | None,
) -> None:
    """Apply iterative user corrections to the last rendered edit plan.

    Examples:

      ff fix --project leon-badass-monologue --shot 14 --reason "that's victor not leon"

      ff fix --project leon-badass-monologue --cue 2 --reason "dialogue too late"

      ff fix --project leon-badass-monologue --pacing "act 2" --reason "too slow"

      ff fix --project leon-badass-monologue --color "too teal"
    """
    proj = Path("projects") / project

    # Resolve plan path
    plan_candidates = [
        proj / plan_name,
        proj / "plans" / plan_name,
        proj / ".edit-plan-v1.json",
    ]
    plan_path = next((p for p in plan_candidates if p.exists()), None)
    if plan_path is None:
        console.print(f"[red]EditPlan not found for project '{project}'. Run the optimizer first.[/red]")
        sys.exit(1)

    console.print(f"[cyan]Loading plan[/cyan]: {plan_path}")
    try:
        plan = EditPlan.from_json(plan_path)
    except Exception as exc:
        console.print(f"[red]Failed to load plan: {exc}[/red]")
        sys.exit(1)

    # Build corrections list
    corrections: list[FeedbackCorrection] = []

    if corrections_file:
        if not corrections_file.exists():
            console.print(f"[red]Corrections file not found: {corrections_file}[/red]")
            sys.exit(1)
        corrections = load_feedback_from_file(corrections_file)
        console.print(f"  Loaded {len(corrections)} corrections from {corrections_file.name}")
    else:
        if shot_id is not None:
            corrections.append(FeedbackCorrection(kind="shot", target_id=str(shot_id), reason=reason or "user flagged"))
        if cue_id is not None:
            corrections.append(FeedbackCorrection(kind="cue", target_id=str(cue_id), reason=reason or "timing issue"))
        if pacing_target is not None:
            corrections.append(FeedbackCorrection(kind="pacing", target_id=pacing_target, reason=reason or "pacing adjustment"))
        if color_desc is not None:
            corrections.append(FeedbackCorrection(kind="color", target_id=color_desc, reason=reason or color_desc))

    if not corrections:
        console.print("[yellow]No corrections specified. Use --shot, --cue, --pacing, or --color.[/yellow]")
        sys.exit(0)

    console.print(f"  Applying {len(corrections)} correction(s)...")
    revised = apply_feedback(plan, corrections)

    # Auto-version the output filename
    if output_name:
        out_path = proj / output_name
    else:
        stem = plan_path.stem
        # Increment version: .edit-plan-v1 -> .edit-plan-v2
        import re as _re
        m = _re.search(r"-v(\d+)$", stem)
        if m:
            new_ver = int(m.group(1)) + 1
            new_stem = stem[:m.start()] + f"-v{new_ver}"
        else:
            new_stem = stem + "-v2"
        out_path = plan_path.parent / f"{new_stem}.json"

    revised.to_json(out_path)

    console.print(f"[green]Revised plan saved[/green]: {out_path}")
    console.print(f"  Shots: {len(revised.shots)}  VO cues: {len(revised.dialogue_placements)}")

    # Show applied deltas
    from rich.table import Table as _Table
    tbl = _Table(title="Applied Corrections")
    tbl.add_column("Kind")
    tbl.add_column("Target")
    tbl.add_column("Reason")
    tbl.add_column("Delta")
    for c in corrections:
        tbl.add_row(c.kind, c.target_id, c.reason[:40], c.applied_delta[:60])
    console.print(tbl)


# ---------- export-nle-pro ----------

@main.command("export-nle-pro")
@click.option("--project", required=True)
@click.option("--plan", "plan_name", default=".edit-plan-v1.json")
@click.option(
    "--format", "fmt",
    type=click.Choice(["fcpxml", "edl", "otio", "premiere_xml"]),
    default="fcpxml",
)
@click.option("--song-structure", "song_structure_name", default=None, help="Song structure JSON filename for beat/drop markers")
@click.option("--audio-track", default=None, help="Mixed audio file path to include")
@click.option("--fps", type=int, default=24)
@click.option("--width", type=int, default=1920)
@click.option("--height", type=int, default=1080)
@click.option("--output", default=None, help="Output filename (auto-named if omitted)")
def export_nle_pro_cmd(
    project: str,
    plan_name: str,
    fmt: str,
    song_structure_name: str | None,
    audio_track: str | None,
    fps: int,
    width: int,
    height: int,
    output: str | None,
) -> None:
    """Export an EditPlan with full marker tracks to an NLE timeline file.

    Adds shot-transition markers, beat/downbeat markers, drop/breath markers,
    dialogue cue markers, SFX suggestions, and per-shot color metadata.
    """
    proj = Path("projects") / project

    plan_candidates = [proj / plan_name, proj / "plans" / plan_name, proj / ".edit-plan-v1.json"]
    plan_path = next((p for p in plan_candidates if p.exists()), None)
    if plan_path is None:
        console.print(f"[red]EditPlan not found.[/red]")
        sys.exit(1)

    plan = EditPlan.from_json(plan_path)
    console.print(f"[cyan]Loaded plan[/cyan]: {plan_path.name}  ({len(plan.shots)} shots, {len(plan.dialogue_placements)} VO cues)")

    # Load song structure for beat/drop markers
    song_data = None
    if song_structure_name:
        song_candidates = [proj / "raw" / song_structure_name, proj / song_structure_name]
        for sp in song_candidates:
            if sp.exists():
                song_data = json.loads(sp.read_text())
                console.print(f"  Loaded song structure: {sp.name}")
                break
    if song_data is None:
        # Auto-detect
        for sp in sorted((proj / "raw").glob("*.song_structure.json")):
            song_data = json.loads(sp.read_text())
            console.print(f"  Auto-detected song structure: {sp.name}")
            break

    out_dir = proj / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    ext_map = {"fcpxml": ".fcpxml", "edl": ".edl", "otio": ".otio", "premiere_xml": ".xml"}
    out_filename = output or f"timeline-pro{ext_map.get(fmt, '.xml')}"
    out_path = out_dir / out_filename

    options: dict = {
        "fps": fps,
        "width": width,
        "height": height,
        "title": f"FandomForge -- {project}",
        "raw_dir": str(proj / "raw"),
    }
    if audio_track:
        for at in [proj / "exports" / audio_track, proj / "raw" / audio_track, Path(audio_track)]:
            if at.exists():
                options["audio_track_path"] = str(at)
                break

    result = export_nle_pro(plan, fmt, out_path, options=options, song_structure_data=song_data)

    console.print(f"[green]Exported[/green]: {result.path}")
    console.print(f"  Format: {result.format}  Markers: {result.markers_count}")
    for w in result.warnings:
        console.print(f"  [yellow]Warning[/yellow]: {w}")


# ---------- ab-render ----------

@main.command("ab-render")
@click.option("--project", required=True)
@click.option("--plan", "plan_name", default=".edit-plan-v1.json")
@click.option("--output-dir", default=None, help="Output directory (defaults to exports/variants/)")
@click.option("--raw-dir", default=None, help="Raw video source directory (enables actual rendering)")
def ab_render_cmd(
    project: str,
    plan_name: str,
    output_dir: str | None,
    raw_dir: str | None,
) -> None:
    """Render 3 variants of an edit plan and produce a comparison bundle.

    Produces variant_a (teal-orange/default pacing), variant_b (desaturated/fast),
    and variant_c (noir/slow) plan JSONs. Renders MP4s if --raw-dir is provided.
    Opens vote.html for side-by-side comparison.
    """
    proj = Path("projects") / project

    plan_candidates = [proj / plan_name, proj / "plans" / plan_name, proj / ".edit-plan-v1.json"]
    plan_path = next((p for p in plan_candidates if p.exists()), None)
    if plan_path is None:
        console.print(f"[red]EditPlan not found.[/red]")
        sys.exit(1)

    plan = EditPlan.from_json(plan_path)
    console.print(f"[cyan]Loaded plan[/cyan]: {plan_path.name}  ({len(plan.shots)} shots)")

    out_dir = Path(output_dir) if output_dir else proj / "exports" / "variants"
    raw = Path(raw_dir) if raw_dir else None

    console.print(f"[cyan]Rendering 3 variants[/cyan] -> {out_dir}")
    bundle = render_variants(plan, output_dir=out_dir, raw_dir=raw)

    for r in bundle.variants:
        status = "[green]rendered[/green]" if r.video_path else "[yellow]plan-only[/yellow]"
        console.print(f"  {r.config.name}: {r.config.description[:60]} {status}")
        for w in r.render_warnings:
            console.print(f"    [yellow]warning[/yellow]: {w}")

    console.print(f"\n[bold]Vote UI[/bold]: {bundle.vote_ui_stub}")
    if bundle.comparison_grid.grid_image_path:
        console.print(f"[bold]Grid[/bold]: {bundle.comparison_grid.grid_image_path}")
    console.print(f"[bold]Bundle metadata[/bold]: {bundle.output_dir / 'bundle.json'}")


# ---------- cluster-styles ----------

@main.command("cluster-styles")
@click.option("--project", required=True)
@click.option("--profiles-dir", "profiles_dir", default=".ref-profiles", help="Profiles subdirectory name")
@click.option("--k", type=int, default=None, help="Force number of clusters (auto-selects if omitted)")
@click.option("--output", default=".style-clusters.json", help="Output filename in project root")
def cluster_styles_cmd(
    project: str,
    profiles_dir: str,
    k: int | None,
    output: str,
) -> None:
    """Cluster reference video profiles into style archetypes.

    Runs K-means (k=3..6 unless forced) on the 8 editorial features extracted
    from each reference profile, picks the best k by silhouette score, and
    outputs a .style-clusters.json file with archetype names and per-cluster
    style templates.
    """
    proj = Path("projects") / project
    pdir = proj / profiles_dir

    if not pdir.exists():
        console.print(f"[red]Profiles directory not found[/red]: {pdir}")
        sys.exit(1)

    json_count = len(list(pdir.glob("*.json")))
    console.print(f"[cyan]Clustering[/cyan] {json_count} profiles in {pdir}")

    try:
        result = cluster_references(pdir, k=k)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    out_path = proj / output
    save_cluster_result(result, out_path, templates_dir=proj)

    console.print(f"\n[bold]k={result.k}[/bold]  silhouette={result.silhouette_score:.4f}  ({result.n_profiles_loaded} profiles)\n")

    from rich.table import Table as _Table
    tbl = _Table(title="Style Archetypes")
    tbl.add_column("Cluster")
    tbl.add_column("Archetype")
    tbl.add_column("Size", justify="right")
    tbl.add_column("BPM", justify="right")
    tbl.add_column("cuts/s", justify="right")
    tbl.add_column("shot_dur", justify="right")
    tbl.add_column("VO%", justify="right")
    for c in result.clusters:
        tbl.add_row(
            str(c.cluster_id),
            c.archetype_name,
            str(c.size),
            f"{c.centroid['tempo_bpm']:.0f}",
            f"{c.centroid['cuts_per_second']:.2f}",
            f"{c.centroid['shot_duration_median']:.2f}s",
            f"{c.centroid['vo_coverage_pct']:.0f}%",
        )
    console.print(tbl)
    console.print(f"\n[green]Saved[/green]: {out_path}")


# ---------- make-edit (master pipeline) ----------

@main.command("make-edit")
@click.option("--project", required=True, type=click.Path(exists=True, path_type=Path),
              help="Project directory (e.g. projects/leon-badass-monologue). "
                   "If it contains a project-config.yaml, that drives everything; "
                   "other flags override specific values.")
@click.option("--song", type=click.Path(exists=True, path_type=Path),
              help="Override the song in project-config.yaml")
@click.option("--output", default="final.mp4", help="Output filename")
@click.option("--template", default=None,
              help="Override narrative template from config")
@click.option("--cluster", default=None,
              help="Override style cluster archetype")
@click.option("--duration", type=float, default=None, help="Target runtime seconds")
@click.option("--song-offset", type=float, default=None, help="Song start offset seconds")
@click.option("--character", default=None, help="Override primary character")
@click.option("--skip-enrich", is_flag=True, help="Skip motion/gaze enrichment (faster)")
@click.option("--skip-qa", is_flag=True, help="Skip QA gate (not recommended)")
@click.option("--export-presets", default=None,
              help="Comma-separated list of export presets (overrides config)")
def make_edit_cmd(
    project: Path,
    song: Path | None,
    output: str,
    template: str | None,
    cluster: str | None,
    duration: float | None,
    song_offset: float | None,
    character: str | None,
    skip_enrich: bool,
    skip_qa: bool,
    export_presets: str | None,
) -> None:
    """End-to-end master pipeline — one command to render the full engine.

    Reads `project-config.yaml` from the project directory and drives
    everything from it. CLI flags override individual fields when provided.
    """
    from fandomforge.master_pipeline import (
        PipelineConfig, run, print_result, from_project_config,
    )

    # Build overrides dict (only include flags that were actually set)
    overrides: dict[str, Any] = {"output_name": output}
    if song is not None:
        overrides["song_path"] = song
    if template is not None:
        overrides["template_name"] = template
    if cluster is not None:
        overrides["cluster_archetype"] = cluster
    if duration is not None:
        overrides["target_duration_sec"] = duration
    if song_offset is not None:
        overrides["song_offset_sec"] = song_offset
    if character is not None:
        overrides["primary_character"] = character
    if export_presets:
        overrides["export_presets"] = [
            p.strip() for p in export_presets.split(",") if p.strip()
        ]

    # Load config file if present
    config_path = project / "project-config.yaml"
    if not config_path.exists():
        config_path = project / "project-config.yml"
    if not config_path.exists():
        config_path = project / "project-config.json"

    if config_path.exists():
        cfg = from_project_config(project, **overrides)
        console.print(f"[cyan]Loaded[/cyan] {config_path.name}: character={cfg.primary_character}, template={cfg.template_name}")
    else:
        # Backward-compat: if no config file, use flags directly
        if song is None:
            console.print("[red]✗[/red] No project-config.yaml and no --song provided")
            sys.exit(1)
        cfg = PipelineConfig(
            project_dir=project,
            song_path=song,
            output_name=output,
            template_name=template or "HauntedVeteran",
            cluster_archetype=cluster or "single-character arc",
            target_duration_sec=duration,
            song_offset_sec=song_offset or 0.0,
            primary_character=character or "leon",
        )

    # Apply skip flags only when they were actually passed — otherwise the
    # config's enrich_motion/enrich_gaze/run_qa values must win. The old
    # unconditional override here clobbered per-project settings, forcing
    # every pipeline to run enrich even when config said `enrich_motion: false`.
    if skip_enrich:
        cfg.enrich_motion = False
        cfg.enrich_gaze = False
    if skip_qa:
        cfg.run_qa = False

    result = run(cfg)
    print_result(result)

    # Auto-run render verifier on every successful pipeline — plan JSON can
    # claim sync anchors but the render might not actually show them. The
    # verifier samples frames + audio and confirms what's on disk matches
    # intent. User rule: "always do that and more to verify as much as
    # possible."
    if result.success and result.final_video and result.final_video.exists():
        try:
            from fandomforge.intelligence.render_verifier import verify_render
            plan_candidates = [
                cfg.project_dir / ".layered-plan-final.json",
                cfg.project_dir / ".edit-plan-final.json",
            ]
            plan_path = next((p for p in plan_candidates if p.exists()), None)
            if plan_path:
                console.print("\n[cyan]Proofing render…[/cyan]")
                report = verify_render(
                    result.final_video, plan_path, cfg.project_dir,
                    api_key=os.environ.get("OPENAI_API_KEY"),
                )
                console.print(report.summary())
                if not report.passed:
                    console.print(
                        "\n[yellow]⚠ Render has quality issues. "
                        "Pipeline stages all passed but the verifier found "
                        "problems in the actual output. See report JSON for "
                        "specific timestamps.[/yellow]"
                    )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]verify step failed: {exc}[/yellow]")

        # Auto-generate NLE timelines alongside the rendered mp4 so the user
        # can open a matching Resolve / Premiere project and hand-tweak
        # without re-running the export command.
        try:
            from fandomforge.intelligence.shot_optimizer import EditPlan
            from fandomforge.intelligence.nle_export_pro import (
                export as export_nle_pro,
            )
            plan_candidates = [
                cfg.project_dir / ".layered-plan-final.json",
                cfg.project_dir / ".edit-plan-final.json",
            ]
            plan_path = next((p for p in plan_candidates if p.exists()), None)
            # Pick most recent song_structure json to match the current song
            song_struct_candidates = sorted(
                cfg.project_dir.glob(".song-structure-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            song_data = None
            if song_struct_candidates:
                try:
                    song_data = json.loads(song_struct_candidates[0].read_text())
                except Exception:
                    song_data = None
            if plan_path:
                video_stem = Path(cfg.output_name).stem
                audio_path = cfg.project_dir / ".assembly-work" / "mixed_audio.wav"
                plan_obj = EditPlan.from_json(plan_path)
                exports_dir = cfg.project_dir / "exports"
                options: dict = {
                    "raw_dir": str(cfg.project_dir / "raw"),
                    "title": f"FandomForge — {cfg.project_dir.name}",
                }
                if audio_path.exists():
                    options["audio_track_path"] = str(audio_path)
                console.print("\n[cyan]Auto-exporting NLE timelines…[/cyan]")
                for fmt in ("fcpxml", "premiere_xml"):
                    ext = "fcpxml" if fmt == "fcpxml" else "xml"
                    out = exports_dir / f"{video_stem}-timeline.{ext}"
                    try:
                        export_nle_pro(
                            plan_obj, fmt, out,
                            options=options, song_structure_data=song_data,
                        )
                        console.print(f"  [green]✓[/green] {out.name}")
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"  [yellow]⚠[/yellow] {fmt}: {exc}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]NLE auto-export skipped: {exc}[/yellow]")

    sys.exit(0 if result.success else 1)


# ---------- grab (download video/audio from any URL) ----------

@main.group()
def grab() -> None:
    """Download video/audio from a URL into a project's raw/ or assets/ folder."""


@grab.command("video")
@click.option("--project", required=True)
@click.option("--url", required=True, help="Any yt-dlp-supported URL (YouTube, Vimeo, Archive.org, direct mp4, etc.)")
@click.option("--resolution", default="1080", help="Max resolution (1080, 720, 480, best). Auto-cascades down on format_unavailable.")
@click.option("--filename", default=None, help="Explicit filename (no extension). Defaults to sanitized title.")
@click.option("--audio-only", is_flag=True, help="Extract audio only. Writes to assets/ instead of raw/.")
@click.option("--no-audio", is_flag=True, help="Keep video, drop audio track. Silent mp4.")
@click.option("--audio-format", default="mp3", help="Audio codec for --audio-only (mp3, m4a, flac, wav, opus).")
@click.option("--cookies-from-browser", default=None,
              type=click.Choice(["chrome", "chromium", "brave", "edge", "firefox",
                                 "safari", "opera", "vivaldi", "whale"], case_sensitive=False),
              help="Pull auth cookies from this browser (for age-restricted / private content).")
@click.option("--cookies", "cookies_file", default=None, type=click.Path(exists=False),
              help="Path to a Netscape-format cookies.txt (alternative to --cookies-from-browser).")
@click.option("--note", default=None, help="Optional free-form note stored in the sidecar (e.g. where this came from).")
@click.option("--no-ingest", is_flag=True, help="Skip scene + transcript extraction after download.")
@click.option("--no-verify", is_flag=True, help="Skip post-download stream verification (ffprobe + silence check).")
def grab_video_cmd(
    project: str,
    url: str,
    resolution: str,
    filename: str | None,
    audio_only: bool,
    no_audio: bool,
    audio_format: str,
    cookies_from_browser: str | None,
    cookies_file: str | None,
    note: str | None,
    no_ingest: bool,
    no_verify: bool,
) -> None:
    """Download a URL into projects/<slug>/. Default: video+audio mp4 into raw/.
    Use --audio-only for mp3 into assets/, or --no-audio for silent mp4.
    For age-restricted or private content, pass --cookies-from-browser chrome
    (or firefox/safari/edge/brave/opera/vivaldi/whale/chromium).
    """
    from fandomforge.sources.download import download_source, DownloadErrorKind

    if audio_only and no_audio:
        console.print("[red]--audio-only and --no-audio are mutually exclusive[/red]")
        sys.exit(1)

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        console.print(f"[red]project not found: {project}[/red]")
        console.print(f"  create it with: ff project new {project}")
        sys.exit(1)

    out_dir = (proj_dir / "assets") if audio_only else (proj_dir / "raw")

    mode_label = "audio-only" if audio_only else ("video-only (no audio)" if no_audio else "video+audio")
    auth_label = ""
    if cookies_from_browser:
        auth_label = f", auth via {cookies_from_browser}"
    elif cookies_file:
        auth_label = f", auth via cookies.txt"
    console.print(f"[cyan]↓[/cyan] {url}  [dim]({mode_label}{auth_label})[/dim]")

    result = download_source(
        url,
        out_dir,
        filename=filename,
        resolution=resolution,
        write_subs=not audio_only,
        auto_subs=not audio_only,
        audio_only=audio_only,
        no_audio=no_audio,
        audio_format=audio_format,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file,
        verify_streams=not no_verify,
    )

    if not result.success:
        kind = result.error_kind.value if result.error_kind else "unknown"
        console.print(f"[red]✗ download failed:[/red] [bold]{kind}[/bold]")
        if result.error_message:
            console.print(f"  {result.error_message}")
        if result.attempts:
            console.print(f"  [dim]attempts: {' → '.join(result.attempts)}[/dim]")
        if result.error_kind in (DownloadErrorKind.AGE_RESTRICTED, DownloadErrorKind.PRIVATE) and not (cookies_from_browser or cookies_file):
            console.print("  [yellow]hint:[/yellow] retry with [bold]--cookies-from-browser chrome[/bold] (or firefox/safari/edge/brave/opera/vivaldi/whale/chromium)")
        if result.error_kind in (
            DownloadErrorKind.MISSING_VIDEO_STREAM,
            DownloadErrorKind.MISSING_AUDIO_STREAM,
            DownloadErrorKind.SILENT_AUDIO,
        ):
            # Purge the broken file + sidecar so the next attempt starts clean
            if result.path and result.path.exists():
                try:
                    result.path.unlink()
                    console.print(f"  [dim]removed broken file: {result.path.name}[/dim]")
                except OSError:
                    pass
            console.print("  [yellow]hint:[/yellow] try a different URL for this source")
        if result.stderr:
            console.print(f"  [dim]{result.stderr[-400:]}[/dim]")
        sys.exit(1)

    if not result.path:
        console.print(f"[red]✗ download failed:[/red] no file produced")
        sys.exit(1)

    import hashlib
    data = result.path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    import json as _json
    from datetime import datetime, timezone
    sidecar = result.path.with_suffix(result.path.suffix + ".grab.json")
    sidecar.write_text(_json.dumps({
        "url": url,
        "mode": "audio_only" if audio_only else ("no_audio" if no_audio else "video_audio"),
        "resolution_requested": None if audio_only else resolution,
        "resolution_actual": result.final_resolution,
        "audio_format": audio_format if audio_only else None,
        "note": note,
        "sha256": sha,
        "bytes": len(data),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "route": result.route,
        "attempts": result.attempts,
        "subtitles_dropped": result.subtitles_dropped,
        "format_fallback_used": result.format_fallback_used,
        "has_video_stream": result.has_video_stream,
        "has_audio_stream": result.has_audio_stream,
        "audio_mean_dbfs": result.audio_mean_dbfs,
    }, indent=2) + "\n")

    console.print(f"  [green]✓[/green] {result.path.name} ({len(data):,} bytes, sha256 {sha[:12]}...)")
    console.print(f"  sidecar: {sidecar.name}")
    if result.has_video_stream is not None or result.has_audio_stream is not None:
        v = "✓" if result.has_video_stream else "—"
        a = "✓" if result.has_audio_stream else "—"
        dbfs = f" @ {result.audio_mean_dbfs:.1f} dB" if result.audio_mean_dbfs is not None else ""
        console.print(f"  streams: video {v}  audio {a}{dbfs}")
    if result.subtitles_dropped:
        console.print("  [yellow]⚠[/yellow] subtitles unavailable (429 or missing) — media was still pulled")
    if result.format_fallback_used and result.final_resolution:
        console.print(f"  [yellow]⚠[/yellow] requested {resolution}p unavailable, fell back to {result.final_resolution}p")

    if audio_only or no_ingest:
        return

    console.print(f"[cyan]↓[/cyan] running ingest (scenes + transcript)")
    rc = subprocess.call(["ff", "ingest", str(result.path), "--project", project], cwd=Path.cwd())
    if rc == 0:
        console.print(f"  [green]✓[/green] ingested")
    else:
        console.print(f"  [yellow]ingest exit {rc} — rerun with `ff ingest` later[/yellow]")


@grab.command("song")
@click.option("--project", required=True)
@click.option("--url", default=None, help="Direct URL. Use --search instead to auto-pick the best audio upload.")
@click.option("--search", "search_query", default=None,
              help="Search query (e.g. 'Fall Out Boy Centuries'). Auto-picks the best-ranked audio upload — Official Audio > Visualizer > Lyric Video > Music Video.")
@click.option("--pick", type=int, default=1,
              help="Which ranked search result to use (1 = top). Only with --search.")
@click.option("--dry-run", is_flag=True,
              help="With --search, print rankings and exit without downloading.")
@click.option("--no-warn-on-music-video", is_flag=True,
              help="Suppress the warning when --url points at a Music Video upload.")
@click.option("--filename", default="song", help="Filename (no extension). Defaults to 'song'.")
@click.option("--audio-format", default="mp3", help="Audio codec (mp3, m4a, flac, wav, opus).")
@click.option("--cookies-from-browser", default=None,
              type=click.Choice(["chrome", "chromium", "brave", "edge", "firefox",
                                 "safari", "opera", "vivaldi", "whale"], case_sensitive=False),
              help="Pull auth cookies from this browser (for age-restricted / private content).")
@click.option("--cookies", "cookies_file", default=None, type=click.Path(exists=False),
              help="Path to a Netscape-format cookies.txt (alternative to --cookies-from-browser).")
@click.option("--note", default=None, help="Optional free-form note stored in the sidecar.")
def grab_song_cmd(
    project: str,
    url: str | None,
    search_query: str | None,
    pick: int,
    dry_run: bool,
    no_warn_on_music_video: bool,
    filename: str,
    audio_format: str,
    cookies_from_browser: str | None,
    cookies_file: str | None,
    note: str | None,
) -> None:
    """Download an audio track into projects/<slug>/assets/song.*.

    Two ways to specify the source:
      --search "Artist Track"   auto-picks the best-ranked upload (Official Audio / Visualizer > Lyric > MV)
      --url https://...         use an explicit URL (warns if it scores like a Music Video)

    Wrapper over `ff grab video --audio-only`. Uses the same robust download
    layer (retries, rate-limit backoff, typed errors, cookies, format fallback).
    """
    from fandomforge.sources.download import download_source, DownloadErrorKind
    from fandomforge.sources.song_search import (
        rank_song_source, search_song, SongKind,
    )

    if not url and not search_query:
        console.print("[red]must pass --url or --search[/red]")
        sys.exit(1)
    if url and search_query:
        console.print("[red]use --url OR --search, not both[/red]")
        sys.exit(1)

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        console.print(f"[red]project not found: {project}[/red]")
        sys.exit(1)

    quality = None

    if search_query:
        console.print(f"[cyan]searching[/cyan] {search_query}")
        candidates = search_song(search_query, max_results=10)
        if not candidates:
            console.print(f"[red]no results for[/red] {search_query}")
            console.print("  is yt-dlp installed? run `yt-dlp --version` to check.")
            sys.exit(1)
        console.print(f"  top rankings:")
        for i, c in enumerate(candidates[:5], start=1):
            marker = "←" if i == pick else " "
            console.print(
                f"  {marker} {i}. [{c.quality.score:3d}] "
                f"[dim]{c.quality.kind.value:16s}[/dim] "
                f"{c.title[:60]:<60s} "
                f"[dim]({c.duration_sec:5.1f}s, {c.uploader[:20]})[/dim]"
            )
        if dry_run:
            console.print("\n[yellow]dry-run, not downloading[/yellow]")
            return
        if pick < 1 or pick > len(candidates):
            console.print(f"[red]--pick {pick} out of range (1..{len(candidates)})[/red]")
            sys.exit(1)
        chosen = candidates[pick - 1]
        url = chosen.url
        quality = chosen.quality
        console.print(f"[cyan]picked[/cyan] #{pick}: [bold]{chosen.title}[/bold]")
        console.print(f"  [dim]{url}[/dim]")
    else:
        # User passed --url explicitly. Probe quality and warn if it looks like MV.
        quality = rank_song_source(url)
        if quality.score > 0:
            console.print(
                f"  quality: [{quality.score}] {quality.kind.value} — "
                f"[dim]{quality.reason[:100]}[/dim]"
            )
        if quality.kind == SongKind.MUSIC_VIDEO and not no_warn_on_music_video:
            console.print(
                "[yellow]⚠ this URL is a Music Video upload.[/yellow] "
                "Music videos often have SFX / radio edits / truncated endings."
            )
            console.print(
                "  [yellow]suggest:[/yellow] retry with "
                "[bold]--search \"<artist> <track>\"[/bold] to auto-pick a "
                "cleaner audio source, or pass [bold]--no-warn-on-music-video[/bold] to proceed anyway."
            )

    assets = proj_dir / "assets"

    auth_label = ""
    if cookies_from_browser:
        auth_label = f", auth via {cookies_from_browser}"
    elif cookies_file:
        auth_label = ", auth via cookies.txt"
    console.print(f"[cyan]↓[/cyan] {url} [dim](audio-only{auth_label})[/dim]")

    result = download_source(
        url,
        assets,
        filename=filename,
        audio_only=True,
        audio_format=audio_format,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file,
    )

    if not result.success or not result.path:
        kind = result.error_kind.value if result.error_kind else "unknown"
        console.print(f"[red]✗ download failed:[/red] [bold]{kind}[/bold]")
        if result.error_message:
            console.print(f"  {result.error_message}")
        if result.attempts:
            console.print(f"  [dim]attempts: {' → '.join(result.attempts)}[/dim]")
        if result.error_kind in (DownloadErrorKind.AGE_RESTRICTED, DownloadErrorKind.PRIVATE) and not (cookies_from_browser or cookies_file):
            console.print("  [yellow]hint:[/yellow] retry with [bold]--cookies-from-browser chrome[/bold] (or firefox/safari/edge/brave/opera/vivaldi/whale/chromium)")
        if result.stderr:
            console.print(f"  [dim]{result.stderr[-400:]}[/dim]")
        sys.exit(1)

    _write_song_sidecar(
        result.path, url, result.route or "yt-dlp", audio_format, note,
        quality=quality,
    )
    console.print(f"  [green]✓[/green] {result.path.name} ({result.path.stat().st_size:,} bytes)")
    console.print(f"  sidecar: {result.path.name}.grab.json")


@grab.group("cookies")
def grab_cookies() -> None:
    """Export / inspect browser cookies for use with --cookies."""


@grab_cookies.command("export")
@click.option("--browser", required=True,
              type=click.Choice(["chrome", "chromium", "brave", "edge", "firefox",
                                 "safari", "opera", "vivaldi", "whale"], case_sensitive=False))
@click.option("--output", "-o", default="cookies.txt", type=click.Path(),
              help="Where to write the Netscape-format cookies.txt.")
def grab_cookies_export_cmd(browser: str, output: str) -> None:
    """Export a reusable cookies.txt from your local browser.

    The resulting file works with `ff grab video --cookies <file>` and is
    portable across machines (useful for servers / CI). Requires you to be
    logged into the relevant site(s) in the chosen browser.
    """
    from fandomforge.sources.download import export_cookies_from_browser

    target = Path(output)
    console.print(f"[cyan]↓[/cyan] exporting cookies from {browser} → {target}")
    result = export_cookies_from_browser(browser, target)
    if not result.success:
        console.print(f"[red]✗ export failed:[/red] {result.error_message}")
        if result.stderr:
            console.print(f"  [dim]{result.stderr[-400:]}[/dim]")
        sys.exit(1)
    console.print(f"  [green]✓[/green] wrote {target} ({target.stat().st_size:,} bytes)")
    console.print(f"  use with: [bold]ff grab video --cookies {target} --url ...[/bold]")


def _write_song_sidecar(
    path: Path, url: str, via: str, audio_format: str, note: str | None,
    quality=None,
) -> None:
    import hashlib
    import json as _json
    from datetime import datetime, timezone

    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    sidecar = path.with_suffix(path.suffix + ".grab.json")
    payload: dict = {
        "url": url,
        "mode": "audio_only",
        "via": via,
        "audio_format": audio_format,
        "note": note,
        "sha256": sha,
        "bytes": len(data),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if quality is not None:
        payload["source_quality"] = {
            "kind": quality.kind.value if hasattr(quality.kind, "value") else str(quality.kind),
            "score": quality.score,
            "reason": quality.reason,
            "title": quality.title,
            "uploader": quality.uploader,
        }
    sidecar.write_text(_json.dumps(payload, indent=2) + "\n")


# ---------- review (post-render grade + test) ----------


@main.command("review")
@click.option("--project", required=True, help="Project slug to review.")
@click.option("--video", default="graded.mp4",
              help="Which render to review (in exports/). Defaults to graded.mp4.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON instead of table.")
@click.option("--save/--no-save", default=True,
              help="Write the report to data/post-render-review.json.")
def review_cmd(project: str, video: str, as_json: bool, save: bool) -> None:
    """Grade a rendered edit: technical / visual / audio / structural / shot list.

    Run this after every render. It surfaces black frames, clipping, duration
    mismatches, source reuse, and anything else worth a second look before
    the cut leaves the rough-cut stage.
    """
    import json as _json
    from fandomforge.review import review_rendered_edit

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        console.print(f"[red]project not found: {project}[/red]")
        sys.exit(1)

    try:
        report = review_rendered_edit(proj_dir, video_name=video)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if save:
        out = proj_dir / "data" / "post-render-review.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(report.to_dict(), indent=2) + "\n")

    if as_json:
        print(_json.dumps(report.to_dict(), indent=2))
        if report.overall_verdict == "fail":
            sys.exit(1)
        return

    color = {"green": "green", "yellow": "yellow", "red": "red"}[report.overall]
    # Letter grade color: A* green, B* cyan, C* yellow, D/F red
    grade_color = {
        "A+": "green", "A": "green", "A-": "green",
        "B+": "cyan", "B": "cyan", "B-": "cyan",
        "C+": "yellow", "C": "yellow", "C-": "yellow",
        "D+": "red", "D": "red", "D-": "red", "F": "red",
    }.get(report.grade, "white")
    console.print(
        f"\n[bold]review for[/bold] {report.project_slug}  "
        f"([{color}]{report.overall.upper()}[/{color}])  "
        f"grade [bold {grade_color}]{report.grade}[/bold {grade_color}]  "
        f"score [{grade_color}]{report.score:.1f}/100[/{grade_color}]"
    )
    console.print(f"  video: {report.video_path}")
    console.print()
    table = Table(show_header=True)
    table.add_column("dimension")
    table.add_column("verdict")
    table.add_column("score", justify="right")
    table.add_column("findings")
    for d in report.dimensions:
        color_map = {"pass": "green", "warn": "yellow", "fail": "red"}
        col = color_map[d.verdict]
        findings = "\n".join(d.findings) if d.findings else "[dim]—[/dim]"
        table.add_row(
            d.name,
            f"[{col}]{d.verdict}[/{col}]",
            f"{d.score:.0f}",
            findings,
        )
    console.print(table)
    console.print(f"\n[bold]{report.ship_recommendation}[/bold]")
    if save:
        console.print(f"\n[dim]full report: {proj_dir}/data/post-render-review.json[/dim]")

    if report.overall_verdict == "fail":
        sys.exit(1)


# ---------- library (global media library) ----------


@main.group("library")
def library_group() -> None:
    """Link folders of movies once, reuse across every project.

    Typical flow:
        ff library link /Volumes/Movies --name home
        ff library scan
        ff library list
        ff autopilot --project my-edit --from-library --theme "..." --song ...
    """


@library_group.command("link")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--name", default=None, help="Short label for this root (defaults to folder name).")
@click.option("--auto-fandom",
              type=click.Choice(["dir1", "dir2", "filename-before-year", "manual"]),
              default="dir1",
              help="How to infer the fandom for files under this root.")
def library_link_cmd(path: Path, name: str | None, auto_fandom: str) -> None:
    """Register a folder of movies as a source root."""
    from fandomforge import library as lib

    try:
        root = lib.link_root(path, name=name, auto_fandom_rule=auto_fandom)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    console.print(f"[green]✓[/green] linked root [bold]{root.name}[/bold] → {root.path}")
    console.print(f"  fandom rule: {root.auto_fandom_rule}")
    console.print(f"  next: [bold]ff library scan[/bold] to discover + ingest files")


@library_group.command("list")
@click.option("--sources", is_flag=True, help="List individual source files, not just roots.")
@click.option("--fandom", default=None, help="Filter by fandom label.")
@click.option("--status", type=click.Choice(["pending", "in_progress", "done", "failed"]),
              default=None, help="Filter by ingest status.")
def library_list_cmd(sources: bool, fandom: str | None, status: str | None) -> None:
    """Show linked roots, per-fandom counts, and (with --sources) every file."""
    from fandomforge import library as lib

    roots = lib.list_roots()
    if not roots:
        console.print("[yellow]no library roots linked[/yellow]")
        console.print("  link one with: [bold]ff library link /path/to/movies[/bold]")
        return

    table = Table(title="Library roots")
    table.add_column("name")
    table.add_column("path")
    table.add_column("fandom rule")
    table.add_column("files")
    for r in roots:
        count = len(lib.list_sources(root_name=r.name))
        table.add_row(r.name, str(r.path), r.auto_fandom_rule, str(count))
    console.print(table)

    counts = lib.fandom_counts()
    if counts:
        console.print("\n[bold]By fandom:[/bold]")
        for f, n in counts.items():
            console.print(f"  {f:<30s} {n:>6d}")

    if sources:
        rows = lib.list_sources(fandom=fandom, status=status)
        if not rows:
            console.print("\n[dim]no sources match the filter[/dim]")
            return
        console.print(f"\n[bold]Sources ({len(rows)}):[/bold]")
        for s in rows[:200]:
            status_col = {
                "pending": "[yellow]pending[/yellow]",
                "in_progress": "[cyan]in-progress[/cyan]",
                "done": "[green]done[/green]",
                "failed": "[red]failed[/red]",
            }.get(s.ingest_status, s.ingest_status)
            title = f"{s.title}" + (f" ({s.year})" if s.year else "")
            console.print(
                f"  {status_col}  [bold]{s.fandom or '—':<25s}[/bold] {title[:40]:<40s} [dim]{s.path}[/dim]"
            )
        if len(rows) > 200:
            console.print(f"  [dim]... and {len(rows) - 200} more[/dim]")


@library_group.command("scan")
@click.option("--name", default=None, help="Scan only this root (default: all).")
@click.option("--ingest/--no-ingest", default=True,
              help="Run ff ingest on new files (default on). --no-ingest just indexes paths.")
def library_scan_cmd(name: str | None, ingest: bool) -> None:
    """Recursively walk linked folders and index every media file.

    If --ingest is on (default), also runs scene detection + transcription +
    CLIP embeddings for any files not yet ingested. These artifacts are
    shared across projects via the global derived/ cache.
    """
    from fandomforge import library as lib

    roots = [lib.get_root(name)] if name else lib.list_roots()
    if name and roots[0] is None:
        console.print(f"[red]no root named {name}[/red]")
        sys.exit(1)

    total = 0
    pending_sources: list = []
    for root in roots:
        if root is None:
            continue
        console.print(f"[cyan]scanning[/cyan] {root.name} → {root.path}")
        result = lib.scan_root(root.name)
        console.print(
            f"  discovered {result.discovered}, added {result.added}, "
            f"already indexed {result.already_indexed}"
        )
        if result.errors:
            for err in result.errors[:5]:
                console.print(f"  [red]error:[/red] {err}")
        total += result.added
        pending_sources.extend(
            lib.list_sources(root_name=root.name, status="pending")
        )

    if not ingest:
        console.print(
            f"\n[yellow]--no-ingest[/yellow] — indexed {total} files, "
            f"{len(pending_sources)} still pending. "
            f"Run [bold]ff library scan[/bold] without the flag to ingest."
        )
        return

    if not pending_sources:
        console.print("\n[green]✓[/green] every file ingested")
        return

    console.print(
        f"\n[cyan]ingesting {len(pending_sources)} pending source(s)[/cyan] "
        f"into the global derived/ cache..."
    )
    ok_count = 0
    fail_count = 0
    for src in pending_sources:
        lib.set_ingest_status(src.id, "in_progress")
        # Fire ff ingest per file. The subprocess already reuses cached
        # derived/<blake2>/ artifacts via content-hash.
        rc = subprocess.call(
            [
                "ff", "ingest", str(src.path),
                "--project", str(lib.library_root()),  # library root as pseudo-project
                "--fandom", src.fandom or "Unknown",
                "--source-type", src.source_type or "movie",
                "--title", src.title or "",
                "--no-characters",
            ],
            cwd=Path.cwd(),
        )
        if rc == 0:
            lib.set_ingest_status(src.id, "done")
            ok_count += 1
            console.print(f"  [green]✓[/green] {src.title or src.path.name}")
        else:
            lib.set_ingest_status(src.id, "failed", f"ff ingest exit {rc}")
            fail_count += 1
            console.print(f"  [red]✗[/red] {src.path.name}")

    console.print(
        f"\n[bold]scan complete:[/bold] {ok_count} ingested, {fail_count} failed"
    )


@library_group.command("tag")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--fandom", required=True, help="Fandom label to apply.")
def library_tag_cmd(file: Path, fandom: str) -> None:
    """Override the auto-inferred fandom for one file."""
    from fandomforge import library as lib

    try:
        updated = lib.tag_source(file, fandom=fandom)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("  the file must first be indexed. run: [bold]ff library scan[/bold]")
        sys.exit(1)
    console.print(f"[green]✓[/green] tagged {updated.path.name} as [bold]{fandom}[/bold]")


@library_group.command("unlink")
@click.argument("name")
@click.option("--delete-sources", is_flag=True,
              help="Also drop the source rows (derived/ cache stays on disk).")
def library_unlink_cmd(name: str, delete_sources: bool) -> None:
    """Forget a linked root. Ingested artifacts under derived/ remain."""
    from fandomforge import library as lib

    count = lib.unlink_root(name, delete_sources=delete_sources)
    verb = "removed" if delete_sources else "unlinked (kept indexed)"
    console.print(f"[green]✓[/green] {verb} {count} source(s) under root [bold]{name}[/bold]")


@library_group.command("show")
@click.argument("query")
@click.option("--limit", default=20, type=int)
def library_show_cmd(query: str, limit: int) -> None:
    """Preview which sources match a free-text fandom/title query."""
    from fandomforge import library as lib

    q = query.lower()
    hits = [
        s for s in lib.list_sources()
        if (s.fandom and q in s.fandom.lower())
        or (s.title and q in s.title.lower())
    ]
    if not hits:
        console.print(f"[yellow]no matches for {query!r}[/yellow]")
        return
    console.print(f"[bold]{len(hits)} match(es) for {query!r}[/bold]")
    for s in hits[:limit]:
        console.print(
            f"  [bold]{s.fandom or '—':<20s}[/bold] {s.title or s.path.name:<40s} "
            f"[dim]{s.ingest_status}[/dim]"
        )


@library_group.command("summary")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def library_summary_cmd(as_json: bool) -> None:
    """Quick machine-readable summary of the library. Used by the dashboard API."""
    import json as _json
    from fandomforge import library as lib

    roots = lib.list_roots()
    counts = lib.fandom_counts()
    all_sources = lib.list_sources()
    totals = {
        "sources": len(all_sources),
        "pending":     sum(1 for s in all_sources if s.ingest_status == "pending"),
        "in_progress": sum(1 for s in all_sources if s.ingest_status == "in_progress"),
        "done":        sum(1 for s in all_sources if s.ingest_status == "done"),
        "failed":      sum(1 for s in all_sources if s.ingest_status == "failed"),
    }
    payload = {
        "index_path": str(lib.library_db_path()),
        "exists": lib.library_db_path().exists(),
        "roots": [
            {
                "name": r.name,
                "path": str(r.path),
                "auto_fandom_rule": r.auto_fandom_rule,
                "added_at": r.added_at,
                "file_count": sum(1 for s in all_sources if s.root_name == r.name),
            }
            for r in roots
        ],
        "fandoms": [{"fandom": k, "count": v} for k, v in counts.items()],
        "totals": totals,
    }
    if as_json:
        print(_json.dumps(payload, indent=2))
        return
    console.print(_json.dumps(payload, indent=2))


# ---------- share (read-only shareable link tokens) ----------

@main.group()
def share() -> None:
    """Generate/revoke opaque share tokens for read-only plan links."""


@share.command("generate")
@click.option("--project", required=True)
@click.option("--note", default="", help="Optional note about who / why this share exists.")
def share_generate_cmd(project: str, note: str) -> None:
    """Generate a 32-byte URL-safe share token for a project."""
    import json as _json
    import secrets
    from datetime import datetime, timezone

    proj_dir = Path("projects") / project
    if not proj_dir.exists():
        console.print(f"[red]project not found: {project}[/red]")
        sys.exit(1)

    token = secrets.token_urlsafe(32)
    data = {
        "schema_version": 1,
        "project_slug": project,
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }
    target = proj_dir / "share.json"
    target.write_text(_json.dumps(data, indent=2) + "\n")
    console.print(f"[green]✓[/green] share token generated")
    console.print(f"  URL path: /share/{token}")
    console.print(f"  Written to: {target}")


@share.command("revoke")
@click.option("--project", required=True)
def share_revoke_cmd(project: str) -> None:
    """Delete the share token for a project (future link opens will 404)."""
    proj_dir = Path("projects") / project
    target = proj_dir / "share.json"
    if target.exists():
        target.unlink()
        console.print(f"[green]✓[/green] share.json removed for {project}")
    else:
        console.print(f"[yellow]no share.json to revoke for {project}[/yellow]")


@share.command("list")
def share_list_cmd() -> None:
    """List all active share tokens."""
    import json as _json
    projects_dir = Path("projects")
    if not projects_dir.exists():
        console.print("[yellow]no projects/ dir[/yellow]")
        return
    table = Table(title="Share tokens")
    table.add_column("project")
    table.add_column("token (first 12)")
    table.add_column("created")
    table.add_column("note")
    for share_file in sorted(projects_dir.glob("*/share.json")):
        try:
            data = _json.loads(share_file.read_text())
            table.add_row(
                data.get("project_slug", share_file.parent.name),
                (data.get("token") or "")[:12] + "...",
                (data.get("created_at") or "")[:19],
                (data.get("note") or "")[:40],
            )
        except Exception:  # noqa: BLE001
            continue
    console.print(table)


# ---------- templates (edit-plan skeletons) ----------

@main.group()
def templates() -> None:
    """Manage edit-plan templates (4-act-hype, mentor-loss, duality, etc)."""


def _templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "templates" / "edit-plans"


@templates.command("list")
def templates_list_cmd() -> None:
    """List available edit-plan templates."""
    import json as _json
    tdir = _templates_dir()
    if not tdir.exists():
        console.print(f"[red]templates dir missing: {tdir}[/red]")
        sys.exit(1)
    table = Table(title="Edit-plan templates")
    table.add_column("name")
    table.add_column("vibe")
    table.add_column("length")
    table.add_column("description")
    for tf in sorted(tdir.glob("*.json")):
        try:
            data = _json.loads(tf.read_text())
        except Exception:  # noqa: BLE001
            continue
        table.add_row(
            data.get("_template_name", tf.stem),
            data.get("_template_vibe", "—"),
            f"{data.get('_template_recommended_length_sec', '—')}s",
            (data.get("_template_description") or "")[:60],
        )
    console.print(table)


@templates.command("apply")
@click.argument("template_name")
@click.option("--project", required=True, help="Project slug to apply to.")
@click.option("--force", is_flag=True, help="Overwrite existing edit-plan.json.")
def templates_apply_cmd(template_name: str, project: str, force: bool) -> None:
    """Write a new edit-plan.json from the given template."""
    import json as _json

    source = _templates_dir() / f"{template_name}.json"
    if not source.exists():
        console.print(f"[red]Template not found: {template_name}[/red]")
        console.print(f"  looking in: {source}")
        console.print(f"  hint: run `ff templates list` to see available templates")
        sys.exit(1)

    data = _json.loads(source.read_text())
    data["project_slug"] = project
    for key in list(data.keys()):
        if key.startswith("_template_"):
            data.pop(key)

    target = Path("projects") / project / "data" / "edit-plan.json"
    if target.exists() and not force:
        console.print(f"[yellow]{target} exists.[/yellow] Use --force to overwrite.")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(_json.dumps(data, indent=2) + "\n")
    tmp.replace(target)

    console.print(
        f"[green]✓[/green] wrote edit-plan from template '{template_name}' → {target}"
    )


# ---------- autopilot (end-to-end one-click flow) ----------

@main.command("autopilot")
@click.option("--project", required=True, help="Project slug (created if absent).")
@click.option("--song", type=click.Path(exists=True, path_type=Path), default=None,
              help="Path to the song audio. Copied into assets/ if not already there.")
@click.option("--sources", default=None, help="Glob for source videos (e.g. 'clips/*.mp4').")
@click.option("--prompt", default="", help="Theme / concept for the edit.")
@click.option("--estimate", is_flag=True, help="Print cost/time estimate and exit without running.")
@click.option("--from-library", is_flag=True,
              help="Pull sources from the global media library (skip local ingest).")
@click.option("--fandom-mix", default=None,
              help="When --from-library is set: comma-separated fandom weights, e.g. 'John Wick:0.4,Mad Max:0.6'.")
def autopilot_cmd(
    project: str,
    song: Path | None,
    sources: str | None,
    prompt: str,
    estimate: bool,
    from_library: bool,
    fandom_mix: str | None,
) -> None:
    """One-click path: prompt + song + sources → beat map → shot list → QA gate.

    Idempotent: rerunning picks up from the last successful step. Progress is
    streamed to projects/<slug>/.history/autopilot.jsonl.

    --from-library pulls source clips from the global media library
    (linked via `ff library link`) instead of expecting them in raw/.
    """
    import json as _json
    from fandomforge.autopilot import run_autopilot, estimate_cost

    if estimate:
        est = estimate_cost(project)
        console.print(_json.dumps(est, indent=2))
        return

    # Parse --fandom-mix "Name:0.4,Other:0.6" into a dict.
    parsed_mix: dict[str, float] | None = None
    if fandom_mix:
        parsed_mix = {}
        for chunk in fandom_mix.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" in chunk:
                name, weight = chunk.rsplit(":", 1)
                try:
                    parsed_mix[name.strip()] = float(weight.strip())
                except ValueError:
                    parsed_mix[name.strip()] = 1.0
            else:
                parsed_mix[chunk] = 1.0

    if from_library and not parsed_mix:
        console.print(
            "[yellow]warning:[/yellow] --from-library without --fandom-mix "
            "will pull every ingested source (no filter)."
        )

    result = run_autopilot(
        project,
        song_path=song,
        source_glob=sources,
        prompt=prompt,
        from_library=from_library,
        fandom_mix=parsed_mix,
    )

    console.print(
        f"\n[bold]autopilot:[/bold] {result['overall_status']} "
        f"({len(result['steps'])} steps)"
    )
    if result["overall_status"] == "failed":
        sys.exit(1)


# ---------- emotion (emotion-arc inference) ----------

@main.group()
def emotion() -> None:
    """Emotion analysis for an edit."""


@emotion.command("arc")
@click.option("--project", required=True)
@click.option("--force", is_flag=True)
def emotion_arc_cmd(project: str, force: bool) -> None:
    """Infer a per-shot emotion arc and write data/emotion-arc.json."""
    import json as _json
    from fandomforge.intelligence.emotion_arc import infer_for_project, detect_dead_zones
    from fandomforge.validation import validate, ValidationError

    try:
        arc = infer_for_project(project)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    try:
        validate(arc, "emotion-arc")
    except ValidationError as exc:
        console.print(f"[red]Arc failed schema validation:[/red]")
        for f in exc.failures[:10]:
            console.print(f"  - {f}")
        sys.exit(1)

    target = Path("projects") / project / "data" / "emotion-arc.json"
    if target.exists() and not force:
        console.print(f"[yellow]{target} exists.[/yellow] Use --force to overwrite.")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(_json.dumps(arc, indent=2) + "\n")
    tmp.replace(target)

    dead = detect_dead_zones(arc)
    console.print(
        f"[green]✓[/green] wrote {len(arc['samples'])} samples → {target}"
    )
    if dead:
        console.print(f"[yellow]dead zones[/yellow] ({len(dead)}):")
        for start, end in dead[:5]:
            console.print(f"  - {start:.1f}s → {end:.1f}s ({end-start:.1f}s flat)")
    else:
        console.print("[dim]no dead zones longer than 20s[/dim]")


# ---------- propose (auto-draft artifacts) ----------

@main.group()
def propose() -> None:
    """Auto-draft artifacts from existing project context."""


@propose.command("shots")
@click.option("--project", required=True)
@click.option("--output", type=click.Path(path_type=Path), default=None,
              help="Output file. Defaults to projects/<slug>/data/shot-list.json.")
@click.option("--force", is_flag=True, help="Overwrite existing shot-list.json.")
@click.option("--dry-run", is_flag=True, help="Print the draft to stdout without writing.")
def propose_shots_cmd(project: str, output: Path | None, force: bool, dry_run: bool) -> None:
    """Draft a shot list from the edit-plan + beat-map + catalog."""
    import json as _json
    from fandomforge.intelligence.shot_proposer import propose_for_project
    from fandomforge.validation import validate, ValidationError

    draft = propose_for_project(project)

    try:
        validate(draft, "shot-list")
    except ValidationError as exc:
        console.print("[red]Draft failed schema validation:[/red]")
        for failure in exc.failures[:10]:
            console.print(f"  - {failure}")
        sys.exit(1)

    if dry_run:
        console.print(_json.dumps(draft, indent=2))
        return

    proj = Path("projects") / project
    target = output or (proj / "data" / "shot-list.json")
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        console.print(f"[yellow]{target} exists.[/yellow] Use --force to overwrite.")
        sys.exit(1)

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(_json.dumps(draft, indent=2) + "\n")
    tmp.replace(target)

    console.print(
        f"[green]✓[/green] drafted {len(draft['shots'])} shots → {target}"
    )
    if any(s["source_id"].startswith("PLACEHOLDER_") for s in draft["shots"]):
        console.print(
            "[yellow]Note:[/yellow] some shots reference PLACEHOLDER_ sources. "
            "Ingest source videos with `ff ingest` first for real source_ids."
        )


# ---------- fixtures (legal test-media library) ----------

@main.group()
def fixtures() -> None:
    """Manage the legal-test-media fixture library."""


@fixtures.command("fetch")
@click.option("--manifest", type=click.Path(exists=True, path_type=Path), default=None,
              help="Path to fixtures manifest. Defaults to tools/tests/fixtures/manifest.json.")
@click.option("--only", multiple=True, help="Fetch only these item ids (repeatable).")
@click.option("--dry-run", is_flag=True, help="Print what would be fetched without downloading.")
def fixtures_fetch_cmd(manifest: Path | None, only: tuple[str, ...], dry_run: bool) -> None:
    """Download legal test fixtures from the curated manifest.

    Every item has a documented license. Media lands under
    tools/tests/fixtures/media/ (gitignored) with a sibling .grab.json.
    """
    import hashlib
    import json as _json
    import urllib.request

    default_manifest = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "manifest.json"
    manifest_path = Path(manifest) if manifest else default_manifest
    if not manifest_path.exists():
        console.print(f"[red]Manifest not found:[/red] {manifest_path}")
        sys.exit(1)

    data = _json.loads(manifest_path.read_text())
    items = data.get("items", [])
    if only:
        selected = set(only)
        items = [it for it in items if it.get("id") in selected]
        if not items:
            console.print(f"[yellow]No items matched:[/yellow] {', '.join(only)}")
            sys.exit(1)

    media_dir = manifest_path.parent / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    ok_count = 0
    skip_count = 0
    fail_count = 0

    for item in items:
        item_id = item.get("id", "<no-id>")
        url = item.get("url", "")
        filename = item.get("filename") or item_id
        license_name = item.get("license", "unspecified")

        target = media_dir / filename
        grab_sidecar = media_dir / (target.name + ".grab.json")

        if dry_run:
            console.print(f"[cyan]would fetch[/cyan] {item_id} → {target.name} ({license_name})")
            continue

        if target.exists() and target.stat().st_size > 0:
            console.print(f"[dim]✓ {item_id}: already cached[/dim]")
            skip_count += 1
            continue

        console.print(f"[cyan]↓ {item_id}[/cyan] {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
                data_bytes = response.read()
            target.write_bytes(data_bytes)
            sha = hashlib.sha256(data_bytes).hexdigest()
            grab_sidecar.write_text(_json.dumps({
                "id": item_id,
                "url": url,
                "license": license_name,
                "license_url": item.get("license_url"),
                "attribution": item.get("attribution"),
                "license_note": item.get("license_note"),
                "sha256": sha,
                "bytes": len(data_bytes),
            }, indent=2) + "\n")
            console.print(f"  [green]✓[/green] {target.name} ({len(data_bytes):,} bytes, sha256 {sha[:12]}...)")
            ok_count += 1
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]✗ fetch failed: {exc}[/red]")
            fail_count += 1

    console.print(
        f"\n[bold]fixtures:[/bold] {ok_count} fetched, {skip_count} cached, {fail_count} failed"
    )
    sys.exit(0 if fail_count == 0 else 1)


@fixtures.command("list")
@click.option("--manifest", type=click.Path(exists=True, path_type=Path), default=None)
def fixtures_list_cmd(manifest: Path | None) -> None:
    """List fixtures in the manifest with cache status."""
    import json as _json
    default_manifest = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "manifest.json"
    manifest_path = Path(manifest) if manifest else default_manifest
    data = _json.loads(manifest_path.read_text())
    media_dir = manifest_path.parent / "media"

    table = Table(title="Fixtures", show_lines=False)
    table.add_column("id")
    table.add_column("kind")
    table.add_column("tier")
    table.add_column("license")
    table.add_column("cached")
    for item in data.get("items", []):
        filename = item.get("filename") or item["id"]
        cached = (media_dir / filename).exists()
        table.add_row(
            item.get("id", "—"),
            item.get("kind", "—"),
            item.get("tier", "—"),
            item.get("license", "—"),
            "[green]yes[/green]" if cached else "[dim]no[/dim]",
        )
    console.print(table)


if __name__ == "__main__":
    main()
