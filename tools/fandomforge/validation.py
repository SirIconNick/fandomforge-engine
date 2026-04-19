"""Artifact validation — every JSON handoff between pipeline stages runs through here.

Public API:

    validate(data, schema_id)
    validate_file(path, schema_id=None)
    validate_and_write(data, schema_id, path)
    infer_schema_id(path)

Design rules:

- `additionalProperties: false` is enforced in every schema, so typos fail loudly.
- Unknown `schema_id` raises KeyError (loud, not silent).
- Validation errors collect EVERY failure (not just the first), so the caller can
  show a full diagnosis.
- File validation auto-infers schema id from filename stem when omitted.
- `validate_and_write` does atomic write (temp file + rename) so a half-written
  artifact never lands on disk.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as _JSValidationError

from fandomforge.schemas import SCHEMA_IDS, load_schema

__all__ = [
    "ValidationFailure",
    "ValidationError",
    "validate",
    "validate_file",
    "validate_and_write",
    "infer_schema_id",
]


# ---------------------------------------------------------------------------
# Exceptions & result types
# ---------------------------------------------------------------------------


@dataclass
class ValidationFailure:
    """One concrete schema violation."""

    path: str
    message: str
    schema_path: str

    def render(self) -> str:
        loc = self.path or "<root>"
        return f"{loc}: {self.message}"


class ValidationError(Exception):
    """Raised by `validate` when any rule fails. Carries the full failure list."""

    def __init__(self, schema_id: str, failures: list[ValidationFailure]):
        self.schema_id = schema_id
        self.failures = failures
        super().__init__(self.render())

    def render(self) -> str:
        lines = [
            f"Validation failed against schema '{self.schema_id}' "
            f"({len(self.failures)} failure{'s' if len(self.failures) != 1 else ''}):"
        ]
        for f in self.failures:
            lines.append(f"  - {f.render()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Filename → schema id inference
# ---------------------------------------------------------------------------


_FILENAME_TO_SCHEMA: dict[str, str] = {
    "beat-map.json": "beat-map",
    "beat_map.json": "beat-map",
    "beatmap.json": "beat-map",
    "project-config.json": "project-config",
    "project-config.yaml": "project-config",
    "project-config.yml": "project-config",
    "catalog.json": "catalog",
    "shot-list.json": "shot-list",
    "shots.json": "shot-list",
    "color-plan.json": "color-plan",
    "color-plan.yaml": "color-plan",
    "transition-plan.json": "transition-plan",
    "audio-plan.json": "audio-plan",
    "title-plan.json": "title-plan",
    "edit-plan.json": "edit-plan",
    "source-catalog.json": "source-catalog",
    "transcript.json": "transcript",
    "scenes.json": "scenes",
    "qa-report.json": "qa-report",
}


def infer_schema_id(path: str | os.PathLike[str]) -> str:
    """Infer schema id from a filename. Raises KeyError if it can't be inferred."""
    name = Path(path).name
    if name in _FILENAME_TO_SCHEMA:
        return _FILENAME_TO_SCHEMA[name]
    # Allow `<anything>.beat-map.json` patterns too (useful for per-source artifacts).
    for key, sid in _FILENAME_TO_SCHEMA.items():
        if name.endswith(key):
            return sid
    raise KeyError(
        f"Could not infer schema id from filename '{name}'. "
        f"Pass schema_id explicitly. Known schemas: {', '.join(SCHEMA_IDS)}"
    )


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


def _jsonschema_path_to_pointer(js_path: list[Any]) -> str:
    """Format a jsonschema `absolute_path` deque as a readable dotted pointer."""
    parts: list[str] = []
    for piece in js_path:
        if isinstance(piece, int):
            parts.append(f"[{piece}]")
        else:
            parts.append(str(piece))
    out = ""
    for i, p in enumerate(parts):
        if p.startswith("["):
            out += p
        elif i == 0:
            out = p
        else:
            out += f".{p}"
    return out


def _collect_failures(validator: Draft202012Validator, data: Any) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        failures.append(
            ValidationFailure(
                path=_jsonschema_path_to_pointer(list(err.absolute_path)),
                message=_format_error_message(err),
                schema_path=_jsonschema_path_to_pointer(list(err.absolute_schema_path)),
            )
        )
    return failures


def _format_error_message(err: _JSValidationError) -> str:
    """Rewrite common jsonschema errors into human readable guidance."""
    if err.validator == "additionalProperties":
        # Pull the offending key out of the message.
        unexpected = err.message.split("properties ")[-1].split(" were unexpected")[0]
        return (
            f"Unexpected key(s) {unexpected} — schema is strict. "
            f"Remove or add to schema if intentional."
        )
    if err.validator == "required":
        missing = err.message.split("'")[1] if "'" in err.message else "<unknown>"
        return f"Missing required key '{missing}'"
    if err.validator == "enum":
        choices = ", ".join(repr(x) for x in err.validator_value)
        return f"Value {err.instance!r} is not one of allowed values: [{choices}]"
    if err.validator == "const":
        return f"Value must be exactly {err.validator_value!r}, got {err.instance!r}"
    if err.validator == "pattern":
        return f"Value {err.instance!r} does not match required pattern {err.validator_value}"
    if err.validator == "type":
        return f"Wrong type: got {type(err.instance).__name__}, expected {err.validator_value}"
    if err.validator in ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum"):
        return f"{err.message} (got {err.instance!r})"
    return err.message


def validate(data: Any, schema_id: str) -> None:
    """Validate `data` against the schema identified by `schema_id`.

    Raises:
        KeyError: unknown schema_id
        ValidationError: validation failed (contains full failure list)
    """
    schema = load_schema(schema_id)
    validator = Draft202012Validator(schema)
    failures = _collect_failures(validator, data)
    if failures:
        raise ValidationError(schema_id, failures)


def validate_file(
    path: str | os.PathLike[str],
    schema_id: str | None = None,
) -> None:
    """Load and validate a JSON or YAML file. Infers schema id if omitted."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Artifact not found: {p}")
    sid = schema_id or infer_schema_id(p)
    data = _load_json_or_yaml(p)
    validate(data, sid)


def _load_json_or_yaml(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    return json.loads(text)


def validate_and_write(
    data: Any,
    schema_id: str,
    path: str | os.PathLike[str],
    *,
    indent: int = 2,
) -> Path:
    """Validate `data` and write to `path` atomically.

    The file is written to a temp file in the same directory and then renamed,
    so a half-written artifact never lands on disk. If validation fails, the
    target file is never touched.

    Returns the final Path.
    """
    validate(data, schema_id)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{out.name}.",
        suffix=".tmp",
        dir=str(out.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, out)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return out
