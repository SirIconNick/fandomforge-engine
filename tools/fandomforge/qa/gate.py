"""QA gate orchestrator.

Each rule is a small function with a stable id. The gate loads every rule,
runs them against the project artifacts, collects results, and either
returns a green-lit qa-report.json or blocks with specific evidence.

Rules ship as individual modules under `fandomforge/qa/rules/` so they are
independently testable. A rule returns a RuleResult with one of:

    status = "pass"       -> rule satisfied
    status = "warn"       -> informational; NLE export still proceeds
    status = "fail"       -> BLOCK export (only overridden by override_reason)
    status = "skipped"    -> prerequisite missing; not counted
    status = "overridden" -> failed but user provided a reason
"""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from fandomforge import __version__
from fandomforge.validation import validate, validate_and_write

logger = logging.getLogger(__name__)


__all__ = [
    "GateContext",
    "RuleResult",
    "QAGate",
    "run_gate",
    "rule",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class RuleResult:
    id: str
    name: str
    level: str  # "block" | "warn" | "info"
    status: str  # "pass" | "warn" | "fail" | "skipped" | "overridden"
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    override_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "status": self.status,
        }
        if self.message:
            out["message"] = self.message
        if self.evidence:
            out["evidence"] = self.evidence
        if self.override_reason:
            out["override_reason"] = self.override_reason
        return out


@dataclass
class GateContext:
    """Everything a rule can see about the project."""

    project_dir: Path
    project_slug: str
    edit_plan: dict[str, Any] | None = None
    beat_map: dict[str, Any] | None = None
    shot_list: dict[str, Any] | None = None
    source_catalog: dict[str, Any] | None = None
    color_plan: dict[str, Any] | None = None
    transition_plan: dict[str, Any] | None = None
    audio_plan: dict[str, Any] | None = None
    title_plan: dict[str, Any] | None = None
    # CLI overrides (list of rule ids to override with reasons).
    overrides: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


class _Rule(Protocol):
    id: str
    name: str
    level: str

    def __call__(self, ctx: GateContext) -> RuleResult: ...


_RULES: list[Callable[[GateContext], RuleResult]] = []


def rule(
    rule_id: str,
    name: str,
    level: str = "block",
) -> Callable[[Callable[[GateContext], RuleResult]], Callable[[GateContext], RuleResult]]:
    """Decorator that registers a QA rule.

    The decorated function may return a RuleResult directly, OR a tuple
    (status, message, evidence) and we'll fill in the id/name/level.
    """
    def _wrap(fn: Callable[[GateContext], Any]) -> Callable[[GateContext], RuleResult]:
        def _run(ctx: GateContext) -> RuleResult:
            out = fn(ctx)
            if isinstance(out, RuleResult):
                out.id = out.id or rule_id
                out.name = out.name or name
                out.level = out.level or level
                return out
            status, message, evidence = (
                out[0] if len(out) > 0 else "pass",
                out[1] if len(out) > 1 else "",
                out[2] if len(out) > 2 else {},
            )
            return RuleResult(
                id=rule_id, name=name, level=level,
                status=status, message=message, evidence=evidence,
            )
        _run.id = rule_id
        _run.name = name
        _run.level = level
        _RULES.append(_run)
        return _run
    return _wrap


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------


class QAGate:
    """Runs all registered rules. Call `run()` to get a schema-valid
    qa-report dict."""

    def __init__(self, ctx: GateContext):
        self.ctx = ctx

    @staticmethod
    def _load_rules() -> None:
        """Import every module under fandomforge.qa.rules so @rule decorators fire."""
        import fandomforge.qa.rules as pkg
        for mod in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"fandomforge.qa.rules.{mod.name}")

    def run(self, *, stage: str = "pre-export") -> dict[str, Any]:
        self._load_rules()
        results: list[RuleResult] = []
        for fn in _RULES:
            try:
                res = fn(self.ctx)
            except Exception as e:
                logger.exception("rule %s crashed", getattr(fn, "id", "?"))
                res = RuleResult(
                    id=getattr(fn, "id", "unknown"),
                    name=getattr(fn, "name", "unknown"),
                    level="block",
                    status="fail",
                    message=f"Rule crashed: {e}",
                )
            # Apply CLI overrides.
            if res.status == "fail" and res.id in self.ctx.overrides:
                res.status = "overridden"
                res.override_reason = self.ctx.overrides[res.id]
            results.append(res)

        summary = _summarize(results)
        # Gate status: any unresolved block-level fail -> "fail".
        blocked = any(
            r.status == "fail" and r.level == "block"
            for r in results
        )
        warned = any(r.status in {"warn", "fail"} for r in results)
        status = "fail" if blocked else ("warn" if warned else "pass")

        out: dict[str, Any] = {
            "schema_version": 1,
            "project_slug": self.ctx.project_slug,
            "stage": stage,
            "status": status,
            "rules": [r.to_dict() for r in results],
            "summary": summary,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": f"ff qa gate ({__version__})",
        }
        validate(out, "qa-report")
        return out


def _summarize(results: list[RuleResult]) -> dict[str, int]:
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    warned = sum(1 for r in results if r.status == "warn")
    failed = sum(1 for r in results if r.status == "fail")
    overridden = sum(1 for r in results if r.status == "overridden")
    return {
        "total": total,
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "overridden": overridden,
    }


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def _safe_load(path: Path, schema_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    validate(data, schema_id)
    return data


def build_context(
    project_dir: Path,
    overrides: dict[str, str] | None = None,
) -> GateContext:
    data = project_dir / "data"
    ctx = GateContext(
        project_dir=project_dir,
        project_slug=project_dir.name,
        overrides=overrides or {},
    )
    ctx.edit_plan = _safe_load(data / "edit-plan.json", "edit-plan")
    ctx.beat_map = _safe_load(data / "beat-map.json", "beat-map")
    ctx.shot_list = _safe_load(data / "shot-list.json", "shot-list")
    ctx.source_catalog = _safe_load(data / "source-catalog.json", "source-catalog")
    ctx.color_plan = _safe_load(data / "color-plan.json", "color-plan")
    ctx.transition_plan = _safe_load(data / "transition-plan.json", "transition-plan")
    ctx.audio_plan = _safe_load(data / "audio-plan.json", "audio-plan")
    ctx.title_plan = _safe_load(data / "title-plan.json", "title-plan")
    if ctx.edit_plan and ctx.edit_plan.get("project_slug"):
        ctx.project_slug = ctx.edit_plan["project_slug"]
    return ctx


def run_gate(
    project_dir: Path,
    *,
    overrides: dict[str, str] | None = None,
    stage: str = "pre-export",
    write_to: Path | None = None,
) -> dict[str, Any]:
    """Build the context, run all rules, optionally write qa-report.json.

    Returns the report dict (schema-valid). Raises no exception on failures —
    status is encoded in the returned dict so callers can decide exit codes.
    """
    ctx = build_context(project_dir, overrides=overrides)
    gate = QAGate(ctx)
    report = gate.run(stage=stage)
    if write_to is not None:
        validate_and_write(report, "qa-report", write_to)
    return report
