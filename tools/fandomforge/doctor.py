"""`ff doctor` — environment diagnostics.

Checks every dependency the pipeline relies on and prints a green-or-red
status per item. Modeled on `omnivore doctor`.

Run it before you start a new project to catch missing binaries, missing
models, or a stale venv.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix_hint: str = ""


def check_python() -> Check:
    import sys
    ok = sys.version_info >= (3, 13)
    return Check(
        name="Python",
        ok=ok,
        detail=f"{sys.version.split()[0]}",
        fix_hint="Install Python 3.13+ (brew install python@3.13)",
    )


def check_ffmpeg() -> Check:
    p = shutil.which("ffmpeg")
    if not p:
        return Check(name="ffmpeg", ok=False, fix_hint="brew install ffmpeg")
    try:
        out = subprocess.run(
            [p, "-version"], capture_output=True, text=True, timeout=10
        ).stdout.splitlines()[0]
    except Exception:
        out = p
    return Check(name="ffmpeg", ok=True, detail=out)


def check_module(module_name: str, fix: str) -> Check:
    try:
        if module_name == "madmom":
            # madmom 0.16.x needs the numpy 2 / python 3.10 compatibility patch
            # applied before any of its modules import.
            from fandomforge.audio.beat import _patch_for_madmom
            _patch_for_madmom()
        mod = importlib.import_module(module_name)
        version = getattr(mod, "__version__", "installed")
        return Check(name=module_name, ok=True, detail=str(version))
    except (ImportError, AttributeError) as exc:
        return Check(name=module_name, ok=False, detail=str(exc), fix_hint=fix)


def check_whisper_models(cache_root: Path) -> Check:
    """Whether a Whisper model is cached. Informational — missing just means
    the first transcription will download ~80MB+."""
    cache = cache_root / "whisper"
    pts = sorted(cache.glob("*.pt")) if cache.exists() else []
    detail = ", ".join(p.stem for p in pts) if pts else "empty (will download on first transcription)"
    return Check(
        name="whisper model cache",
        ok=True,  # not fatal
        detail=detail,
    )


def check_openclip_cache(cache_root: Path) -> Check:
    """Informational. Missing cache isn't a failure — first run downloads."""
    cache = cache_root / "open_clip"
    exists = cache.exists() and any(cache.rglob("*.bin"))
    return Check(
        name="open_clip cache",
        ok=True,  # not fatal; we want doctor to stay green without models
        detail=f"{cache} {'populated' if exists else 'empty (will download on first match)'}",
    )


def check_dependency_stack(cache_root: Path) -> list[Check]:
    """The full dep check list."""
    return [
        check_python(),
        check_ffmpeg(),
        check_module("librosa", "pip install librosa"),
        check_module("numpy", "pip install numpy"),
        check_module("scipy", "pip install scipy"),
        check_module("jsonschema", "pip install jsonschema"),
        check_module("yaml", "pip install pyyaml"),
        check_module("whisper", "pip install openai-whisper"),
        check_module("scenedetect", "pip install 'scenedetect[opencv]'"),
        check_module("open_clip", "pip install open_clip_torch"),
        check_module("torch", "pip install torch"),
        check_module("PIL", "pip install Pillow"),
        check_module("face_recognition", "pip install face_recognition (needs dlib)"),
        check_module("madmom", "pip install --no-build-isolation madmom"),
        check_module("demucs", "pip install demucs"),
        check_whisper_models(cache_root),
        check_openclip_cache(cache_root),
    ]


def check_optional_api_keys() -> list[Check]:
    keys = [
        ("ANTHROPIC_API_KEY", "Needed for dashboard expert chat"),
        ("OPENAI_API_KEY", "Needed for TTS / GPT ranking commands"),
        ("JINA_API_KEY", "Optional; higher rate limits for Omnivore lore fetching"),
    ]
    return [
        Check(
            name=key,
            ok=bool(os.environ.get(key)),
            detail="set" if os.environ.get(key) else "not set",
            fix_hint=f"export {key}=... if you need: {note}",
        )
        for key, note in keys
    ]


def check_omnivore() -> Check:
    from fandomforge.integrations.omnivore import omnivore_available
    ok = omnivore_available()
    return Check(
        name="omnivore",
        ok=ok,
        detail="adapter available" if ok else "not found",
        fix_hint="optional; install Omnivore at ~/Projects/omnivore to enable `ff lore fetch`",
    )
