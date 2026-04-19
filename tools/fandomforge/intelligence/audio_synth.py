"""Generate custom risers, impacts, transitions via Meta's AudioCraft.

Wraps facebookresearch/audiocraft (MusicGen + AudioGen) so callers can
produce project-specific SFX without carrying a library of static wavs.
Like voice_isolator, this degrades gracefully when AudioCraft isn't
installed — callers can always fall back to the built-in SFX layer.

Install (heavy: ~4 GB of model files + PyTorch):

    pip install audiocraft
    # First use will auto-download ~2 GB MusicGen + ~1.5 GB AudioGen weights

Typical use inside FandomForge:

    from fandomforge.intelligence.audio_synth import AudioSynth
    synth = AudioSynth()
    if synth.available():
        riser = synth.generate_sfx(
            "rising dramatic tension riser, cinematic, 2 seconds",
            duration_sec=2.0,
            out_path=project_dir / "sfx" / "custom-riser.wav",
        )

The module is import-safe even when audiocraft is absent — is_available()
returns False and generate_* methods are no-ops.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def is_available() -> bool:
    """Check whether audiocraft is importable in this environment."""
    try:
        import audiocraft  # noqa: F401
        return True
    except ImportError:
        return False


def availability_report() -> dict[str, object]:
    out: dict[str, object] = {"audiocraft_importable": is_available()}
    if not out["audiocraft_importable"]:
        out["install_hint"] = "pip install audiocraft"
        return out
    try:
        import audiocraft
        out["version"] = getattr(audiocraft, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        out["version"] = "unknown"
    try:
        import torch
        out["torch_version"] = torch.__version__
        out["cuda_available"] = torch.cuda.is_available()
        if hasattr(torch.backends, "mps"):
            out["mps_available"] = torch.backends.mps.is_available()
    except ImportError:
        out["torch"] = "missing"
    return out


# ---------------------------------------------------------------------------
# Model wrappers (singleton per model name)
# ---------------------------------------------------------------------------

@dataclass
class _Loaded:
    kind: str  # "musicgen" | "audiogen"
    name: str
    model: object


_MODEL_CACHE: dict[str, _Loaded] = {}


def _pick_device_str() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _load(kind: str, name: str) -> Optional[_Loaded]:
    """Load + cache. Returns None when audiocraft is unavailable."""
    key = f"{kind}:{name}"
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if not is_available():
        return None

    device = _pick_device_str()
    try:
        if kind == "musicgen":
            from audiocraft.models import MusicGen
            m = MusicGen.get_pretrained(name, device=device)
        elif kind == "audiogen":
            from audiocraft.models import AudioGen
            m = AudioGen.get_pretrained(name, device=device)
        else:
            raise ValueError(f"unknown model kind: {kind}")
    except Exception as exc:  # noqa: BLE001
        logger.error("audiocraft load failed for %s: %s", key, exc)
        return None

    _MODEL_CACHE[key] = _Loaded(kind=kind, name=name, model=m)
    return _MODEL_CACHE[key]


# ---------------------------------------------------------------------------
# Public generate API
# ---------------------------------------------------------------------------

@dataclass
class SynthResult:
    ok: bool
    path: Optional[Path]
    kind: str
    prompt: str
    duration_sec: float
    reason: str = ""


def generate_music(
    prompt: str,
    out_path: Path,
    *,
    duration_sec: float = 8.0,
    model_name: str = "facebook/musicgen-small",
    sample_rate_hz: int = 48000,
) -> SynthResult:
    """Generate a music bed from a text prompt.

    Useful for intro/outro stings when you don't have a licensed clip.
    MusicGen quality is decent for under-10-second beds; longer outputs
    tend to lose coherence.

    Returns SynthResult; on failure out_path is not created and
    result.ok == False. Always check .ok before using .path.
    """
    loaded = _load("musicgen", model_name)
    if loaded is None:
        return SynthResult(False, None, "music", prompt, duration_sec,
                           reason="audiocraft unavailable")
    try:
        loaded.model.set_generation_params(duration=duration_sec)
        wav_tensor = loaded.model.generate([prompt])
    except Exception as exc:  # noqa: BLE001
        return SynthResult(False, None, "music", prompt, duration_sec,
                           reason=f"generate failed: {exc}")
    return _write_wav(wav_tensor, loaded.model.sample_rate, out_path,
                      target_sr=sample_rate_hz, kind="music",
                      prompt=prompt, duration_sec=duration_sec)


def generate_sfx(
    prompt: str,
    out_path: Path,
    *,
    duration_sec: float = 2.0,
    model_name: str = "facebook/audiogen-medium",
    sample_rate_hz: int = 48000,
) -> SynthResult:
    """Generate a sound effect or riser from a text prompt.

    AudioGen is better than MusicGen for non-musical sounds — impacts,
    risers, whooshes, UI stingers. Keep duration short (1-4 seconds) for
    cleanest output.
    """
    loaded = _load("audiogen", model_name)
    if loaded is None:
        return SynthResult(False, None, "sfx", prompt, duration_sec,
                           reason="audiocraft unavailable")
    try:
        loaded.model.set_generation_params(duration=duration_sec)
        wav_tensor = loaded.model.generate([prompt])
    except Exception as exc:  # noqa: BLE001
        return SynthResult(False, None, "sfx", prompt, duration_sec,
                           reason=f"generate failed: {exc}")
    return _write_wav(wav_tensor, loaded.model.sample_rate, out_path,
                      target_sr=sample_rate_hz, kind="sfx",
                      prompt=prompt, duration_sec=duration_sec)


def _write_wav(
    wav_tensor,
    model_sr: int,
    out_path: Path,
    *,
    target_sr: int,
    kind: str,
    prompt: str,
    duration_sec: float,
) -> SynthResult:
    """Write a generated tensor to a wav, resampling to target_sr."""
    try:
        import torchaudio  # type: ignore
    except ImportError:
        return SynthResult(False, None, kind, prompt, duration_sec,
                           reason="torchaudio missing")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # wav_tensor shape: (batch, channels, time); pick first in batch
        sample = wav_tensor[0].cpu()
        if model_sr != target_sr:
            import torchaudio.functional as F
            sample = F.resample(sample, model_sr, target_sr)
        torchaudio.save(str(out_path), sample, target_sr)
    except Exception as exc:  # noqa: BLE001
        return SynthResult(False, None, kind, prompt, duration_sec,
                           reason=f"save failed: {exc}")
    return SynthResult(True, out_path, kind, prompt, duration_sec)


# ---------------------------------------------------------------------------
# Convenience — synthesize a small catalog of common edit SFX from presets
# ---------------------------------------------------------------------------

_SFX_PRESETS: dict[str, tuple[str, float]] = {
    "tension-riser": ("rising tension riser, cinematic swell, no music", 2.0),
    "impact-boom": ("deep cinematic impact boom, low sub-rumble, dramatic", 1.2),
    "whoosh-transition": ("cinematic whoosh transition, mid-range, short", 0.9),
    "reverse-riser": ("reverse riser sweep, suspenseful, 2 seconds", 2.0),
    "sub-drop": ("sub-bass drop for action scene, modern trailer style", 1.5),
    "glass-shatter": ("glass shatter impact, dramatic, cinematic", 1.0),
    "heartbeat": ("slow tense heartbeat, bass drum, steady pulse", 4.0),
}


def synthesize_sfx_pack(
    output_dir: Path,
    *,
    presets: list[str] | None = None,
    sample_rate_hz: int = 48000,
) -> list[SynthResult]:
    """Generate a pack of common tribute-edit SFX into output_dir.

    Useful one-liner to populate projects/<slug>/sfx/ with riser/impact
    variants tuned to the project's mood. Skips presets that fail; every
    SynthResult tells you what happened.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if presets is None:
        presets = list(_SFX_PRESETS.keys())

    out: list[SynthResult] = []
    for key in presets:
        if key not in _SFX_PRESETS:
            out.append(SynthResult(False, None, "sfx", key, 0,
                                   reason=f"unknown preset: {key}"))
            continue
        prompt, dur = _SFX_PRESETS[key]
        target = output_dir / f"{key}.wav"
        out.append(generate_sfx(
            prompt, target, duration_sec=dur, sample_rate_hz=sample_rate_hz,
        ))
    return out


# ---------------------------------------------------------------------------
# Mood-aware generator — picks an SFX prompt from the project's mood
# ---------------------------------------------------------------------------

def prompt_for_mood(mood: str, kind: str = "riser") -> str:
    """Map a mood string to an AudioCraft prompt for the given SFX kind."""
    m = mood.lower()
    if kind == "riser":
        if any(x in m for x in ("sad", "melancholy", "grief")):
            return "slow orchestral riser, melancholic strings, emotional swell"
        if any(x in m for x in ("tense", "dread", "anxious")):
            return "suspense-building riser, cinematic dread, tension"
        if any(x in m for x in ("triumph", "hope", "uplift")):
            return "triumphant orchestral riser, uplifting, hopeful swell"
        if any(x in m for x in ("angry", "aggressive")):
            return "aggressive cinematic riser, distorted metal, impact"
        return "cinematic riser, dramatic, generic"
    if kind == "impact":
        if "sad" in m or "melancholy" in m:
            return "soft low impact, orchestral, emotional"
        if "tense" in m:
            return "sharp low impact, dread, cinematic"
        return "dramatic cinematic impact"
    return f"cinematic {kind}"
