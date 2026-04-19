# Installing AudioSep for voice isolation

AudioSep is the language-queried source separator we wrap in `voice_isolator.py`. When installed, FandomForge can extract "only Dean's voice" from a source clip that has music or other characters in the background. When not installed, the pipeline still works — it just skips the isolation step.

Installing AudioSep is optional. Skip this unless you're hitting a Whisper-verification dropout rate above 30% on a new project (your lines keep failing because background music is masking the dialogue).

---

## Requirements

- 3.4 GB of disk for checkpoints
- Python 3.10+ with PyTorch 1.13+ (any 2.x works too, despite what the repo says)
- One of:
  - NVIDIA GPU with 3 GB+ VRAM (fastest)
  - Apple Silicon (M1/M2/M3 — runs on MPS, ~0.5–1× realtime)
  - x86 CPU (~3–8× slower than realtime, fine for batch)

---

## Install steps

```bash
# 1. Clone
mkdir -p ~/third-party
cd ~/third-party
git clone https://github.com/Audio-AGI/AudioSep.git
cd AudioSep

# 2. Install deps (ignore their environment.yml — it's Linux+CUDA-11.6 pinned)
pip install torch torchaudio librosa soundfile numpy scipy pyyaml \
    transformers==4.28.1 huggingface-hub lightning torchlibrosa \
    ftfy braceexpand webdataset h5py

# 3. Download checkpoints from HuggingFace
mkdir -p checkpoint
cd checkpoint

# Separator (1.18 GB)
curl -L -o audiosep_base_4M_steps.ckpt \
  https://huggingface.co/spaces/Audio-AGI/AudioSep/resolve/main/checkpoint/audiosep_base_4M_steps.ckpt

# CLAP text/audio encoder (2.19 GB)
curl -L -o music_speech_audioset_epoch_15_esc_89.98.pt \
  https://huggingface.co/spaces/Audio-AGI/AudioSep/resolve/main/checkpoint/music_speech_audioset_epoch_15_esc_89.98.pt

cd ../..
```

FandomForge finds the repo automatically at `~/third-party/AudioSep`. To use a different location, set `AUDIOSEP_REPO=/absolute/path` in your environment.

---

## Verify

```bash
cd "/Users/damato/Video Project"
source .venv/bin/activate

python -c "
import sys; sys.path.insert(0, 'tools')
from fandomforge.intelligence import voice_isolator
print('Available:', voice_isolator.is_available())
print('Report:', voice_isolator.availability_report())
"
```

Expected output (when installed):
```
Available: True
Report: {'repo_path': '/Users/.../third-party/AudioSep', 'env_var': None,
         'pipeline_py': True, 'checkpoint_dir': '...', 'separator_ckpt': True,
         'clap_ckpt': True}
```

---

## Use

Add the flag to any `vo-extract` run:

```bash
ff vo-extract --project dean-winchester-renegades \
  --isolate-voice --voice-query "a gruff male voice"
```

The `--voice-query` is fed to AudioSep's CLAP encoder. Works best with **acoustic-class descriptions**, not names:

| Query | Works |
|---|---|
| "a gruff male voice" | yes |
| "a woman speaking clearly" | yes |
| "a child's voice" | yes |
| "Dean Winchester" | no — AudioSep doesn't know characters |
| "Jensen Ackles" | no |
| "speech, no music" | yes |
| "dialogue without background music" | yes |

Same setting is available programmatically via `extract_vo_library(isolate_voice=True, voice_query=...)`.

---

## Performance notes

- First run of any isolation downloads roberta-base from HuggingFace (~500 MB, cached after).
- Model loads once per Python process (~6 seconds on M2, ~2 seconds on CUDA). Batching multiple clips in one CLI call amortizes that cost.
- `use_chunk=True` is on by default — required for clips longer than 30 s or GPUs with <4 GB VRAM.
- AudioSep outputs 32 kHz mono; we resample to your target (default 48 kHz) on write.

---

## Known issues

- **AudioSep README is stale.** It shows `pipeline.inference()` — the function was renamed to `separate_audio()`. We call the correct name.
- Their `environment.yml` pulls Linux-only CUDA 11.6 Conda packages. Don't use it. The pip list above is the portable version.
- Results are uneven on non-speech queries. The model was trained on speech/music/environmental categories; "explosion" works, "a specific character's laugh" doesn't.

---

## When to NOT bother

- Source videos are already clean single-speaker audio (game cutscenes, podcasts)
- Whisper verification pass rate is already >80% without it
- You don't have 3.4 GB free
- You're on CPU-only and need interactive extraction speed (use `--no-verify` instead)
