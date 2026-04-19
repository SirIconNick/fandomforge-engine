"""Adobe Premiere Pro project export.

Premiere imports FCPXML 1.11 cleanly (File > Import > <xml>), so we reuse the
FCPXML generator and drop it under exports/premiere/ with a `.prproj`-ready
naming convention and a README pointing the user at the import command.

Native `.prproj` generation requires Adobe ExtendScript/UXP running inside
Premiere itself; we emit a jsx script the user can run inside Premiere to
auto-import and convert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fandomforge.assembly.fcp_project import FCPExportResult, export_fcp_project


@dataclass
class PremiereExportResult:
    project_dir: Path
    fcpxml_result: FCPExportResult
    jsx_path: Path
    readme_path: Path
    warnings: list[str] = field(default_factory=list)


_JSX_TEMPLATE = """// FandomForge auto-import for Adobe Premiere Pro
// Run from Premiere: File > Scripts > Run Script File…

var fcpxml = File("{fcpxml_path}");
if (!fcpxml.exists) {{
    alert("FCPXML not found at: " + fcpxml.fsName);
}} else {{
    app.project.importFiles([fcpxml.fsName]);
    alert("Imported FandomForge project '{slug}'. Check the Project panel for the sequence and bins.");
}}
"""


def export_premiere_project(
    *,
    project_dir: Path,
    shot_list: dict[str, Any],
    source_catalog: dict[str, Any],
    edit_plan: dict[str, Any],
    audio_plan: dict[str, Any] | None = None,
    color_plan: dict[str, Any] | None = None,
    title_plan: dict[str, Any] | None = None,
    beat_map: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
) -> PremiereExportResult:
    slug = shot_list["project_slug"]
    out_root = project_dir / "exports" / "premiere"
    out_root.mkdir(parents=True, exist_ok=True)

    fcp_result = export_fcp_project(
        project_dir=project_dir,
        shot_list=shot_list,
        source_catalog=source_catalog,
        edit_plan=edit_plan,
        audio_plan=audio_plan,
        color_plan=color_plan,
        title_plan=title_plan,
        beat_map=beat_map,
        qa_report=qa_report,
        output_root=out_root,
    )

    jsx_path = out_root / f"{slug}-import.jsx"
    jsx_path.write_text(
        _JSX_TEMPLATE.format(
            fcpxml_path=str(fcp_result.fcpxml_path.resolve()),
            slug=slug,
        ),
        encoding="utf-8",
    )

    readme_path = out_root / "README-open-in-premiere.md"
    readme_path.write_text(
        _README_TEMPLATE.format(
            slug=slug,
            fcpxml=str(fcp_result.fcpxml_path.resolve()),
            jsx=str(jsx_path.resolve()),
        ),
        encoding="utf-8",
    )

    return PremiereExportResult(
        project_dir=out_root,
        fcpxml_result=fcp_result,
        jsx_path=jsx_path,
        readme_path=readme_path,
        warnings=list(fcp_result.warnings),
    )


_README_TEMPLATE = """# Open {slug} in Adobe Premiere Pro

Two paths. Pick whichever you prefer.

## Option A: double-click and import

1. Open Premiere Pro.
2. File > Import… and select `{fcpxml}`.
3. Premiere creates a sequence named `{slug}` inside a new `{slug} (Sequence)` project
   with bins matching the FandomForge layout (`01_Song`, `02_Dialogue`, `03_Sources/`, etc).
4. Check the render-notes.md sidecar before rendering.

## Option B: run the JSX import script from inside Premiere

1. Open Premiere Pro.
2. File > Scripts > Run Script File…
3. Select `{jsx}`.
4. The script imports the FCPXML and surfaces a confirmation dialog.

## After import

- Re-link media if Premiere prompts: point it at `raw/` and `derived/` under this project.
- Lumetri Color presets matching each source are applied as per-clip effects; open
  Lumetri panel to fine-tune.
- Markers on the sequence: green = downbeat, red = drop, yellow = buildup.
- Export presets for the target platform live in `export-presets.json` in this folder.
"""
