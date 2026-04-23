"""Autonomous forensic-training pilot.

One entrypoint that does every scheduled/maintenance step the engine
needs to stay trained and in-touch with the reference corpus — without
the user memorizing individual CLI commands.

Tasks the pilot handles, in order:

  1. Scan ``references/corpus.yaml`` for any videos not yet ingested
     into ``references/<bucket>/forensic/``; download + deconstruct each
     one (respects ``--limit`` per run).
  2. Re-mine every bucket's forensic JSONs into ``reference-priors-mined.json``.
  3. Run cross-video synthesis per bucket → ``bucket-report.md``.
  4. Analyze every per-video forensic into ``*.analysis.md`` beside it.
  5. Bootstrap the training journal from forensic priors if the journal
     is empty for any bucket (cold-start signal).
  6. Re-aggregate training outcomes into mined training priors.

Every step is idempotent — already-processed items get skipped — so the
pilot is safe to run on a cron / startup / post-ingest hook. Returns an
``AutoReport`` summarizing what was done.

CLI entrypoint: ``ff auto`` (see cli.py).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "AutoReport",
    "AutoConfig",
    "run_autopilot",
]


@dataclass
class AutoConfig:
    references_dir: Path = Path("references")
    corpus_file: Path = Path("references/corpus.yaml")
    ingest_limit_per_bucket: int = 2
    skip_ingest: bool = False
    skip_mine: bool = False
    skip_synthesize: bool = False
    skip_analyze: bool = False
    skip_training: bool = False
    buckets: list[str] | None = None
    log_handler: Callable[[str], None] | None = None


@dataclass
class AutoReport:
    started_at: str = ""
    finished_at: str = ""
    ingested: dict[str, int] = field(default_factory=dict)
    ingest_failed: dict[str, list[str]] = field(default_factory=dict)
    mined: dict[str, int] = field(default_factory=dict)
    synthesized: dict[str, int] = field(default_factory=dict)
    analyzed: dict[str, int] = field(default_factory=dict)
    training_bootstrapped: dict[str, int] = field(default_factory=dict)
    training_mined_priors: dict[str, Any] = field(default_factory=dict)
    legacy_migrated: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ingested": self.ingested,
            "ingest_failed": self.ingest_failed,
            "mined": self.mined,
            "synthesized": self.synthesized,
            "analyzed": self.analyzed,
            "training_bootstrapped": self.training_bootstrapped,
            "training_mined_priors": self.training_mined_priors,
            "legacy_migrated": self.legacy_migrated,
            "errors": self.errors,
        }


def run_autopilot(
    config: AutoConfig | None = None,
    *,
    downloader: Callable[[str, Path], bool] | None = None,
    audio_downloader: Callable[[str, Path], bool] | None = None,
) -> AutoReport:
    """Execute the full forensic/training maintenance pipeline.

    ``downloader`` and ``audio_downloader`` default to the yt-dlp wrappers
    from the CLI; pass stubs for testing.
    """
    cfg = config or AutoConfig()
    report = AutoReport(started_at=_now_iso())

    def _log(msg: str) -> None:
        if cfg.log_handler:
            cfg.log_handler(msg)
        else:
            logger.info(msg)

    buckets = _resolve_buckets(cfg)
    if not buckets:
        report.errors.append("no buckets to process — references/ empty and no corpus.yaml")
        report.finished_at = _now_iso()
        return report

    # 1. Ingest new corpus videos
    if not cfg.skip_ingest and cfg.corpus_file.exists():
        _ingest_phase(
            cfg, report, buckets,
            downloader=downloader,
            audio_downloader=audio_downloader,
            log=_log,
        )

    # 2. Re-mine priors per bucket
    if not cfg.skip_mine:
        _mine_phase(cfg, report, buckets, log=_log)

    # 3. Synthesize cross-video reports
    if not cfg.skip_synthesize:
        _synthesize_phase(cfg, report, buckets, log=_log)

    # 4. Per-video analysis markdowns
    if not cfg.skip_analyze:
        _analyze_phase(cfg, report, buckets, log=_log)

    # 5. + 6. Training bootstrap + aggregation
    if not cfg.skip_training:
        _training_phase(cfg, report, buckets, log=_log)

    # 7. Legacy-priors migration — fills bucket-report.json for edit types
    # that exist in the old per-playlist format but haven't been re-ingested
    # into the new per-bucket format yet. Idempotent per bucket.
    try:
        from fandomforge.intelligence.legacy_priors_migrator import migrate_all
        migrated = migrate_all(references_dir=cfg.references_dir, force=False)
        for bucket_name, path in migrated.items():
            if path is not None:
                report.legacy_migrated.setdefault(bucket_name, str(path))
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"legacy-migrate: {type(exc).__name__}: {exc}")

    report.finished_at = _now_iso()
    return report


_NON_BUCKET_DIRS = frozenset({"priors", "forensic", "training", "tmp"})


def _looks_like_bucket(path: Path) -> bool:
    """Heuristic: a bucket directory contains source videos, forensic
    reports, a reference-priors.json, or a catalog file. Shared-asset
    dirs like ``references/priors/`` contain only subdirectories and
    don't belong in the bucket enumeration."""
    if not path.is_dir():
        return False
    if path.name.startswith(".") or path.name in _NON_BUCKET_DIRS:
        return False
    signals = (
        "reference-priors.json",
        "reference-priors-mined.json",
        "bucket-report.json",
        "forensic",
    )
    for s in signals:
        if (path / s).exists():
            return True
    # Any video or forensic-json anywhere under the bucket is enough
    for ext in ("*.mp4", "*.mov", "*.mkv", "*.webm", "*.forensic.json"):
        if any(path.rglob(ext)):
            return True
    return False


def _resolve_buckets(cfg: AutoConfig) -> list[str]:
    if cfg.buckets:
        return list(cfg.buckets)
    buckets: set[str] = set()
    if cfg.corpus_file.exists():
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(cfg.corpus_file.read_text(encoding="utf-8")) or {}
            for name in (data.get("buckets") or {}).keys():
                buckets.add(str(name))
        except (OSError, ImportError, Exception):  # noqa: BLE001
            pass
    if cfg.references_dir.exists():
        for p in cfg.references_dir.iterdir():
            if _looks_like_bucket(p):
                buckets.add(p.name)
    return sorted(buckets)


def _ingest_phase(
    cfg: AutoConfig,
    report: AutoReport,
    buckets: list[str],
    *,
    downloader: Callable[[str, Path], bool] | None,
    audio_downloader: Callable[[str, Path], bool] | None,
    log: Callable[[str], None],
) -> None:
    from fandomforge.intelligence.forensic_deconstructor import (
        ForensicRequest, deconstruct_video,
    )

    if downloader is None or audio_downloader is None:
        # Resolve the canonical yt-dlp helpers from cli.py lazily to avoid
        # a circular import at module load time.
        from fandomforge.cli import _ytdlp_download, _ytdlp_audio_only  # type: ignore
        downloader = downloader or _ytdlp_download
        audio_downloader = audio_downloader or _ytdlp_audio_only

    try:
        import yaml  # type: ignore
    except ImportError:
        report.errors.append("pyyaml missing — skipping ingest phase")
        return
    data = yaml.safe_load(cfg.corpus_file.read_text(encoding="utf-8")) or {}
    corpus_buckets = data.get("buckets") or {}

    for bucket in buckets:
        bucket_corpus = corpus_buckets.get(bucket)
        if not bucket_corpus:
            continue
        out_root = cfg.references_dir / bucket / "forensic"
        out_root.mkdir(parents=True, exist_ok=True)
        todo: list[dict[str, Any]] = []
        for v in (bucket_corpus.get("videos") or []):
            vid = v.get("id")
            url = v.get("url")
            if not vid or not url:
                continue
            forensic_out = out_root / f"{vid}.forensic.json"
            if forensic_out.exists():
                continue
            todo.append(v)
            if len(todo) >= cfg.ingest_limit_per_bucket:
                break

        if not todo:
            continue

        log(f"[ingest] {bucket}: {len(todo)} new video(s)")
        ok = 0
        for v in todo:
            vid = v["id"]
            url = v["url"]
            video_path = out_root / f"{vid}.mp4"
            if not video_path.exists():
                log(f"  · fetching {vid}")
                if not downloader(url, video_path):
                    report.ingest_failed.setdefault(bucket, []).append(vid)
                    log(f"  ✗ {vid} (download failed)")
                    continue
            song_path = out_root / f"{vid}.song.wav"
            if not song_path.exists():
                audio_downloader(url, song_path)
            req = ForensicRequest(
                video_path=video_path,
                video_id=vid,
                bucket=bucket,
                url=url,
                song_audio=song_path if song_path.exists() else None,
                output_path=out_root / f"{vid}.forensic.json",
                progress=lambda msg: log(f"    {msg}"),
            )
            try:
                deconstruct_video(req)
                ok += 1
                log(f"  ✓ {vid}")
            except Exception as exc:  # noqa: BLE001
                report.ingest_failed.setdefault(bucket, []).append(vid)
                log(f"  ✗ {vid}: {type(exc).__name__}: {exc}")
        if ok:
            report.ingested[bucket] = ok


def _mine_phase(
    cfg: AutoConfig,
    report: AutoReport,
    buckets: list[str],
    *,
    log: Callable[[str], None],
) -> None:
    from fandomforge.intelligence.forensic_miner import mine_bucket
    for bucket in buckets:
        bucket_dir = cfg.references_dir / bucket
        if not bucket_dir.exists():
            continue
        try:
            mined = mine_bucket(bucket_dir, bucket_name=bucket)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"mine[{bucket}]: {type(exc).__name__}: {exc}")
            continue
        if mined.sample_count == 0:
            continue
        out = bucket_dir / "reference-priors-mined.json"
        out.write_text(json.dumps(mined.to_dict(), indent=2) + "\n", encoding="utf-8")
        report.mined[bucket] = mined.sample_count
        log(f"[mine] {bucket}: n={mined.sample_count} → {out}")


def _synthesize_phase(
    cfg: AutoConfig,
    report: AutoReport,
    buckets: list[str],
    *,
    log: Callable[[str], None],
) -> None:
    from fandomforge.intelligence.forensic_bucket_synthesizer import (
        synthesize_bucket, format_bucket_report,
    )
    for bucket in buckets:
        bucket_dir = cfg.references_dir / bucket
        if not bucket_dir.exists():
            continue
        mined_priors: dict[str, Any] | None = None
        mined_path = bucket_dir / "reference-priors-mined.json"
        if mined_path.exists():
            try:
                mined_priors = (
                    json.loads(mined_path.read_text(encoding="utf-8"))
                ).get("priors") or {}
            except (OSError, json.JSONDecodeError):
                mined_priors = None
        try:
            syn = synthesize_bucket(bucket_dir, bucket_name=bucket, mined_priors=mined_priors)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"synthesize[{bucket}]: {type(exc).__name__}: {exc}")
            continue
        if syn.sample_count == 0:
            continue
        out = bucket_dir / "bucket-report.md"
        out.write_text(format_bucket_report(syn), encoding="utf-8")
        json_out = bucket_dir / "bucket-report.json"
        json_out.write_text(json.dumps(syn.to_dict(), indent=2) + "\n", encoding="utf-8")
        report.synthesized[bucket] = syn.sample_count
        log(f"[synth] {bucket}: n={syn.sample_count} → {out}")


def _analyze_phase(
    cfg: AutoConfig,
    report: AutoReport,
    buckets: list[str],
    *,
    log: Callable[[str], None],
) -> None:
    from fandomforge.intelligence.forensic_analyst import (
        analyze_forensic, format_markdown,
    )
    for bucket in buckets:
        bucket_dir = cfg.references_dir / bucket
        if not bucket_dir.exists():
            continue
        mined_priors: dict[str, Any] | None = None
        mined_path = bucket_dir / "reference-priors-mined.json"
        if mined_path.exists():
            try:
                mined_priors = (
                    json.loads(mined_path.read_text(encoding="utf-8"))
                ).get("priors") or {}
            except (OSError, json.JSONDecodeError):
                mined_priors = None
        count = 0
        for fp in bucket_dir.rglob("*.forensic.json"):
            analysis_path = fp.with_name(
                fp.stem.replace(".forensic", "") + ".analysis.md"
            )
            if analysis_path.exists() and analysis_path.stat().st_mtime >= fp.stat().st_mtime:
                continue  # up to date
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                result = analyze_forensic(data, bucket_priors=mined_priors)
                analysis_path.write_text(format_markdown(result), encoding="utf-8")
                count += 1
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"analyze[{fp.name}]: {type(exc).__name__}: {exc}")
        if count:
            report.analyzed[bucket] = count
            log(f"[analyze] {bucket}: {count} new report(s)")


def _training_phase(
    cfg: AutoConfig,
    report: AutoReport,
    buckets: list[str],
    *,
    log: Callable[[str], None],
) -> None:
    from fandomforge.intelligence.training_journal import (
        iter_entries, journal_path as _journal_path,
    )
    from fandomforge.intelligence.training_bootstrap import (
        bootstrap_journal_from_forensic,
    )
    from fandomforge.intelligence.outcome_aggregator import aggregate
    from fandomforge.intelligence.mined_priors import load_training_priors

    journal = _journal_path()
    have_entries: dict[str, int] = {}
    if journal.exists():
        for e in iter_entries(journal):
            have_entries[e.edit_type] = have_entries.get(e.edit_type, 0) + 1

    needs_bootstrap = [b for b in buckets if have_entries.get(b, 0) == 0]
    if needs_bootstrap:
        try:
            written = bootstrap_journal_from_forensic(
                references_dir=cfg.references_dir,
                buckets=needs_bootstrap,
                sample_count=5,
                journal_path=journal,
            )
            for b, n in written.items():
                if n:
                    report.training_bootstrapped[b] = n
                    log(f"[train] {b}: bootstrap {n} synthetic entries")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"train-bootstrap: {type(exc).__name__}: {exc}")

    if not journal.exists():
        return

    entries = list(iter_entries(journal))
    if not entries:
        return

    for bucket in buckets + [None]:  # None = global across all buckets
        scoped = [e for e in entries if (bucket is None or e.edit_type == bucket)]
        if len(scoped) < 3:
            continue
        try:
            priors = aggregate(scoped)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"train-agg[{bucket}]: {type(exc).__name__}: {exc}")
            continue
        out_path = (
            journal.parent / "priors.json" if bucket is None
            else journal.parent / f"priors-{bucket}.json"
        )
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(priors.to_dict(), indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            report.errors.append(f"train-write[{bucket}]: {exc}")
            continue
        key = bucket or "__global__"
        report.training_mined_priors[key] = {
            "sample_count": priors.sample_count,
            "avg_overall_score": priors.avg_overall_score,
            "boolean_impacts": len(priors.boolean_impacts),
            "numeric_correlations": len(priors.numeric_correlations),
        }
        log(
            f"[train] {key}: n={priors.sample_count} avg={priors.avg_overall_score:.1f} → {out_path}"
        )

    # Invalidate the training-priors LRU cache so subsequent renders pick up
    # the freshly-written files without a process restart.
    try:
        load_training_priors.cache_clear()
    except AttributeError:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_auto_report(report: AutoReport) -> str:
    """Produce a human-readable summary of a pilot run."""
    lines = [
        f"# Auto-pilot run — {report.started_at}",
        "",
        f"Finished: {report.finished_at}",
        "",
    ]
    def _section(title: str, d: dict[str, Any]) -> None:
        if not d:
            return
        lines.append(f"## {title}")
        lines.append("")
        for k, v in sorted(d.items()):
            lines.append(f"- {k}: {v}")
        lines.append("")
    _section("Ingested", report.ingested)
    _section("Ingest failures", {k: ", ".join(v) for k, v in report.ingest_failed.items()})
    _section("Mined priors", report.mined)
    _section("Synthesized reports", report.synthesized)
    _section("Analyzed videos", report.analyzed)
    _section("Training bootstrapped", report.training_bootstrapped)
    if report.training_mined_priors:
        lines.append("## Training priors")
        lines.append("")
        for bucket, info in sorted(report.training_mined_priors.items()):
            lines.append(
                f"- **{bucket}** — n={info['sample_count']}, "
                f"avg={info['avg_overall_score']:.1f}, "
                f"impacts={info['boolean_impacts']}, "
                f"correlations={info['numeric_correlations']}"
            )
        lines.append("")
    if report.errors:
        lines.append("## Errors / skips")
        lines.append("")
        for err in report.errors:
            lines.append(f"- {err}")
        lines.append("")
    return "\n".join(lines)
