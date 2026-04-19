"""Sony Vegas Pro project export.

Vegas Pro 20+ imports FCPXML via File > Import > FCP XML. We emit the
FCPXML bundle under exports/vegas/ plus a README. Native `.veg` XML could
be generated too but Vegas's XML format has shifted across versions and
the FCPXML path is reliable enough to be the default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fandomforge.assembly.fcp_project import FCPExportResult, export_fcp_project


@dataclass
class VegasExportResult:
    project_dir: Path
    fcpxml_result: FCPExportResult
    readme_path: Path
    warnings: list[str] = field(default_factory=list)


def export_vegas_project(
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
) -> VegasExportResult:
    slug = shot_list["project_slug"]
    out_root = project_dir / "exports" / "vegas"
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
    readme = out_root / "README-open-in-vegas.md"
    readme.write_text(
        f"""# Open {slug} in Sony Vegas Pro

Vegas Pro 20+:

1. File > Import > FCP XML (or drag-drop the .fcpxmld bundle).
2. Vegas reconstructs the timeline: shots on track V1, song on A1, dialogue
   on A2, sfx on A3.
3. Markers and regions are imported with color tags.
4. Check render-notes.md before final encode.
""",
        encoding="utf-8",
    )
    return VegasExportResult(
        project_dir=out_root,
        fcpxml_result=fcp_result,
        readme_path=readme,
        warnings=list(fcp_result.warnings),
    )
