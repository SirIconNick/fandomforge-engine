"""Language-queried voice isolation via AudioSep.

Wraps Audio-AGI/AudioSep so callers can say "give me only Dean's voice from
this clip" without knowing any ML. When AudioSep isn't installed, every
entry point degrades to a no-op pass-through so the pipeline still works —
we log a warning but never crash.

AudioSep install (outside FandomForge — this module only wraps what's there):

    git clone https://github.com/Audio-AGI/AudioSep.git $HOME/third-party/AudioSep
    cd $HOME/third-party/AudioSep
    pip install torch torchaudio librosa soundfile numpy scipy pyyaml \\
        transformers==4.28.1 huggingface-hub lightning torchlibrosa \\
        ftfy braceexpand webdataset h5py

    # Download checkpoints (3.4 GB total):
    # https://huggingface.co/spaces/Audio-AGI/AudioSep/tree/main/checkpoint
    # - audiosep_base_4M_steps.ckpt (1.18 GB)
    # - music_speech_audioset_epoch_15_esc_89.98.pt (2.19 GB)
    # Both land in AudioSep/checkpoint/

Then set AUDIOSEP_REPO=/absolute/path/to/AudioSep or let this module find
the repo at $HOME/third-party/AudioSep automatically.

Note: AudioSep's `pipeline.inference` was renamed to `separate_audio` but
README was never updated. This wrapper calls the real name.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------

_DEFAULT_AUDIOSEP_CANDIDATES = [
    Path.home() / "third-party" / "AudioSep",
    Path.home() / "Projects" / "AudioSep",
    Path.home() / "src" / "AudioSep",
    Path("/opt/AudioSep"),
]


def _find_audiosep_repo() -> Optional[Path]:
    """Locate an AudioSep checkout. Honors AUDIOSEP_REPO env var first."""
    env = os.environ.get("AUDIOSEP_REPO")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "pipeline.py").exists():
            return p
        logger.warning("AUDIOSEP_REPO=%s doesn't contain pipeline.py", env)
    for cand in _DEFAULT_AUDIOSEP_CANDIDATES:
        if (cand / "pipeline.py").exists():
            return cand
    return None


@lru_cache(maxsize=1)
def is_available() -> bool:
    """Return True if AudioSep is discoverable and importable."""
    repo = _find_audiosep_repo()
    if repo is None:
        return False
    # Check checkpoint files exist
    ckpt_dir = repo / "checkpoint"
    needed = [
        "audiosep_base_4M_steps.ckpt",
        "music_speech_audioset_epoch_15_esc_89.98.pt",
    ]
    for f in needed:
        if not (ckpt_dir / f).exists():
            logger.warning(
                "AudioSep repo found at %s but checkpoint missing: %s",
                repo, f,
            )
            return False
    return True


def availability_report() -> dict[str, str | bool | None]:
    """Diagnostic dict — what did we find, what's missing."""
    repo = _find_audiosep_repo()
    report: dict[str, str | bool | None] = {
        "repo_path": str(repo) if repo else None,
        "env_var": os.environ.get("AUDIOSEP_REPO"),
        "pipeline_py": bool(repo and (repo / "pipeline.py").exists()),
    }
    if repo:
        ckpt_dir = repo / "checkpoint"
        report["checkpoint_dir"] = str(ckpt_dir)
        report["separator_ckpt"] = (ckpt_dir / "audiosep_base_4M_steps.ckpt").exists()
        report["clap_ckpt"] = (
            ckpt_dir / "music_speech_audioset_epoch_15_esc_89.98.pt"
        ).exists()
    return report


# ---------------------------------------------------------------------------
# Model loading (singleton — the checkpoints are ~3.4 GB)
# ---------------------------------------------------------------------------

@dataclass
class _AudioSepModel:
    model: object
    device: object
    repo: Path


_MODEL: Optional[_AudioSepModel] = None


def _pick_device():
    """cuda > mps > cpu — returns a torch.device."""
    import torch  # lazy import
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_model(*, force: bool = False) -> Optional[_AudioSepModel]:
    """Load AudioSep once per process. Returns None if unavailable."""
    global _MODEL
    if _MODEL is not None and not force:
        return _MODEL
    if not is_available():
        return None
    repo = _find_audiosep_repo()
    assert repo is not None

    # Make AudioSep importable
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    try:
        from pipeline import build_audiosep  # AudioSep repo module
    except ImportError as exc:
        logger.error("AudioSep import failed: %s", exc)
        return None

    device = _pick_device()
    cfg = repo / "config" / "audiosep_base.yaml"
    ckpt = repo / "checkpoint" / "audiosep_base_4M_steps.ckpt"
    try:
        model = build_audiosep(
            config_yaml=str(cfg),
            checkpoint_path=str(ckpt),
            device=device,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("AudioSep build_audiosep failed: %s", exc)
        return None

    _MODEL = _AudioSepModel(model=model, device=device, repo=repo)
    logger.info("AudioSep loaded on device=%s repo=%s", device, repo)
    return _MODEL


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def isolate_voice(
    input_wav: Path,
    query: str,
    output_wav: Path,
    *,
    target_sr: int = 48000,
    use_chunk: bool = True,
    fallback_copy: bool = True,
) -> bool:
    """Isolate the sound matching `query` from input_wav, write to output_wav.

    Args:
        input_wav: Source audio. Any ffmpeg-readable format.
        query: Natural-language description (e.g. "a man speaking",
            "male voice", "gruff male voice"). AudioSep is most reliable
            with human/music categories; specific-person queries won't work
            — it isolates by acoustic class, not speaker identity.
        output_wav: Destination. Will be `target_sr`-rate mono WAV.
        target_sr: Output sample rate. AudioSep runs at 32 kHz internally;
            we resample on the way out.
        use_chunk: Process in chunks for files > 30s. Required for long VO.
        fallback_copy: If AudioSep unavailable, copy input → output instead
            of failing. Default True so the pipeline keeps working.

    Returns:
        True if isolation actually ran, False if fell back to copy.
    """
    bundle = load_model()
    if bundle is None:
        if fallback_copy:
            output_wav.parent.mkdir(parents=True, exist_ok=True)
            # ffmpeg copy to enforce sample rate + mono
            _ffmpeg_resample(input_wav, output_wav, target_sr)
            logger.debug("AudioSep unavailable; copied %s → %s", input_wav, output_wav)
        return False

    # AudioSep writes a 32 kHz mono wav. We put it in a temp and resample.
    with tempfile.TemporaryDirectory() as td:
        raw_out = Path(td) / "audiosep_raw.wav"
        try:
            from pipeline import separate_audio  # noqa: WPS433
        except ImportError:
            logger.error("AudioSep separate_audio unavailable after load")
            if fallback_copy:
                _ffmpeg_resample(input_wav, output_wav, target_sr)
            return False

        try:
            separate_audio(
                bundle.model,
                str(input_wav),
                query,
                str(raw_out),
                bundle.device,
                use_chunk=use_chunk,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("AudioSep separate_audio failed on %s: %s", input_wav, exc)
            if fallback_copy:
                _ffmpeg_resample(input_wav, output_wav, target_sr)
            return False

        if not raw_out.exists():
            logger.error("AudioSep produced no output for %s", input_wav)
            if fallback_copy:
                _ffmpeg_resample(input_wav, output_wav, target_sr)
            return False

        # Resample to target_sr + apply gentle gain-match
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg_resample(raw_out, output_wav, target_sr)

    return True


def isolate_character_voice(
    input_wav: Path,
    character_description: str,
    output_wav: Path,
    **kw,
) -> bool:
    """Convenience wrapper that tags the query with generic voice phrasing.

    Pass the character's TYPE (e.g. "a gruff American man", "a young woman")
    rather than their name — AudioSep doesn't know names, only acoustic
    classes. If you only have a name, try 'a man speaking' or 'a woman
    speaking' as a baseline — you'll still get most-voice-isolation benefit.
    """
    query = f"a voice of {character_description}" if character_description else "a man speaking"
    return isolate_voice(input_wav, query, output_wav, **kw)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _ffmpeg_resample(src: Path, dst: Path, sample_rate: int) -> bool:
    """ffmpeg resample src -> dst at the given sample rate, mono, PCM s16."""
    if shutil.which("ffmpeg") is None:
        # Best-effort copy — pipeline should be able to limp without ffmpeg
        shutil.copy2(src, dst)
        return False
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-ar", str(sample_rate),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        shutil.copy2(src, dst)
        return False
