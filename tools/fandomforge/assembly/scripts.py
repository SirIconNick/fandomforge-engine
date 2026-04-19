"""Generate helper shell scripts for a project — download, extract-dialogue, etc."""

from __future__ import annotations

from pathlib import Path

from fandomforge.assembly.dialogue import DialogueEntry
from fandomforge.sources import SourceCatalog


def generate_download_script(
    project_slug: str,
    catalog: SourceCatalog,
    output_path: Path,
    *,
    priority: str = "primary",
    resolution: str = "1080",
) -> int:
    """Write a shell script that downloads all sources at the given priority.

    Returns: the number of download entries written.
    """
    sources = catalog.by_priority(priority)
    sources = [s for s in sources if s.url.startswith(("http://", "https://"))]

    lines: list[str] = [
        "#!/usr/bin/env bash",
        f"# Auto-generated download script for project: {project_slug}",
        f"# Downloads all '{priority}' priority sources at up to {resolution}p.",
        "#",
        "# Run from project root (where pyproject.toml / tools/ live):",
        f"#   ./projects/{project_slug}/scripts/download-all.sh",
        "",
        "set -e",
        "",
        'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"',
        'cd "$ROOT"',
        "",
        'if ! command -v ff >/dev/null 2>&1; then',
        '  echo "ff CLI not found. Activate venv: source .venv/bin/activate"',
        "  exit 1",
        "fi",
        "",
        f'echo "Downloading {len(sources)} {priority} sources for {project_slug}..."',
        f'echo "Resolution cap: {resolution}p"',
        'echo ""',
        "",
    ]

    for i, src in enumerate(sources, start=1):
        safe_title = src.title.replace('"', '\\"')[:80]
        lines.append(f'echo "[{i}/{len(sources)}] {src.id} — {safe_title}"')
        lines.append(
            f'ff sources download --project {project_slug} '
            f'--resolution {resolution} {src.id} || echo "  ↳ failed, continuing..."'
        )
        lines.append("")

    lines += [
        'echo ""',
        'echo "Download complete. Next:"',
        f'echo "  ff roughcut --project {project_slug} --song <song-filename>"',
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")
    output_path.chmod(0o755)
    return len(sources)


def generate_extract_dialogue_script(
    project_slug: str,
    entries: list[DialogueEntry],
    source_hints: dict[str, str],
    output_path: Path,
    character_hints: dict[str, str] | None = None,
) -> int:
    """Generate a shell script that extracts all dialogue WAVs.

    Args:
        project_slug: project folder name
        entries: parsed DialogueEntry list
        source_hints: script source-citation → catalog source-id
                      e.g. {"RE6": "re6-leon-edition"}
        character_hints: character name → default source-id (fallback when no citation)
                         e.g. {"Leon": "re6-leon-edition", "Chris": "re5-full-glp"}
    """
    character_hints = character_hints or {}
    lines: list[str] = [
        "#!/usr/bin/env bash",
        f"# Auto-generated dialogue-extraction script for project: {project_slug}",
        "#",
        "# IMPORTANT: Timestamps below are approximate. Scrub each source video to",
        "# find the EXACT start of each dialogue line, then edit this script before",
        "# running it. Each extract command shows the current best-guess start.",
        "",
        "set -e",
        "",
        'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"',
        'cd "$ROOT"',
        "",
        'if ! command -v ff >/dev/null 2>&1; then',
        '  echo "ff CLI not found. Activate venv: source .venv/bin/activate"',
        "  exit 1",
        "fi",
        "",
        f'echo "Extracting {len(entries)} dialogue audio clips for {project_slug}"',
        'echo "Note: Review timestamps in this script first!"',
        'echo ""',
        "",
    ]

    for i, entry in enumerate(entries, start=1):
        # 1. Try citation-based hint mapping (longest key first)
        src_id = ""
        cite_lower = entry.source.lower().replace(" ", "")
        if cite_lower:
            sorted_hints = sorted(source_hints.items(), key=lambda kv: -len(kv[0]))
            for key, val in sorted_hints:
                if key.lower().replace(" ", "") in cite_lower:
                    src_id = val
                    break
        # 2. Fall back to character-based hint
        if not src_id and entry.character and character_hints:
            char_lower = entry.character.lower().strip()
            for key, val in character_hints.items():
                if key.lower() == char_lower:
                    src_id = val
                    break
        if not src_id:
            src_id = "UNKNOWN_SOURCE_EDIT_THIS"

        line_preview = entry.line[:60].replace('"', '\\"')
        lines.append(f'# [{i}/{len(entries)}] {entry.character}: "{line_preview}..."')
        lines.append(f'#    Source citation: {entry.source or "unknown"}')
        lines.append("#    EDIT THE --start BELOW: scrub the source video to find the exact line")
        lines.append(
            f'ff sources extract --project {project_slug} \\\n'
            f'  --source {src_id} \\\n'
            f'  --start 00:00:00 \\\n'
            f'  --duration {entry.duration_sec:.1f} \\\n'
            f'  --name {entry.audio_filename.replace(".wav", "")} \\\n'
            f'  --audio-only'
        )
        lines.append("")

    lines += [
        'echo ""',
        'echo "Dialogue extraction complete. Check projects/' + project_slug + '/dialogue/"',
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")
    output_path.chmod(0o755)
    return len(entries)
