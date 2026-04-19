# Stack Decision — Why FandomForge Stays Python

Last reviewed: 2026-04-19

This doc exists so the "should we rewrite this in Rust / Go / something else" conversation doesn't have to happen every six months. Write once, point at it next time.

---

## TL;DR

Stay Python. The bottlenecks in this pipeline are not Python. They are:

- `ffmpeg` subprocess (compiled C, not our code)
- `madmom` beat-tracking (C-backed RNN inference)
- `scenedetect` (OpenCV, compiled)
- Anthropic / OpenAI API round trips (network)

Python overhead — imports, orchestration, JSON I/O — is under 10% of wall time on a typical 10-minute render. Rewriting in Rust or Go saves seconds. The rewrite costs weeks and gives up the ML ecosystem we actually rely on (`librosa`, `open_clip_torch`, `face_recognition`, `demucs`, `whisper`, `anthropic` SDK, `openai` SDK). That trade is trash.

What we DO fix: the subprocess recursion inside autopilot (free win), the blocking webhooks (threadpool, also free), and a couple of naive Python loops in color matching (numpy vectorization). Those are inside-Python optimizations, not stack swaps.

---

## Hot-path breakdown

A representative `ff autopilot` run on a 4-minute song + 6 source videos looks like this:

| Stage | Typical time | What's eating the clock | Python share |
|---|---|---|---|
| scaffold + copy_song | <1s | filesystem | 100% (trivial) |
| ingest_sources | 30s–2m | `scenedetect` OpenCV, `whisper` | <5% |
| beat_analyze | 30s–2m | `librosa` + `madmom` RNN | <5% |
| edit_plan (LLM) | 15–60s | Anthropic API latency | <1% |
| propose_shots | 2–10m | scene detection + clip embeddings | <10% |
| emotion_arc | <1s | pure heuristics | ~100% |
| qa_gate | 1–3s | `ffprobe` × N | ~10% |
| roughcut | 5–15m | `ffmpeg` re-encode × N shots | <5% |
| color | 1–3m | `ffmpeg` LUT apply | <5% |
| export (FCPXML) | <1s | XML serialization | ~100% |
| post_render_review | 20–60s | `ffmpeg` blackdetect/freezedetect/loudnorm | <10% |

Python is 100% of the time only for things that already take milliseconds. Anywhere the clock actually moves, we're waiting on a compiled library or a network call.

Subprocess recursion (autopilot shelling out to `ff <sub>` which re-imports torch) is the ONE place Python overhead is measurable — about 20–40s per autopilot run, all in import cost. That's why Phase D refactors those steps to in-process calls. No rewrite needed, just stop shelling out to ourselves.

---

## Alternative 1 — Rust helper for ffmpeg wrapping

**Idea**: replace `subprocess.run(["ffmpeg", ...])` in assembly/orchestrator.py with a small Rust binary that calls libav* directly.

**Cost**: 1–2 weeks to build a reliable wrapper covering the ops we use (trim, concat, overlay, LUT, crossfade, mix). Another week to productionize (error handling, progress callbacks, cross-platform builds).

**Benefit**: Saves the fork/exec cost per ffmpeg call. Measured: ~50–200ms per call on macOS. A 30-shot render has ~30 ffmpeg invocations. Total savings: 1.5–6 seconds on a 10-minute render. That's 0.1–1%.

**Verdict**: Not worth it. The win is in the noise. The loss is a whole new build toolchain, a second language to maintain, and a new category of bugs (FFI, memory, thread safety).

Could reconsider if ffmpeg invocation count grows 10×. It won't.

---

## Alternative 2 — PyAV instead of subprocess ffmpeg

**Idea**: use `PyAV` (Python bindings for libav) to skip the subprocess boundary entirely. Same libav underneath, just no fork/exec.

**Cost**: 1 week to port assembly + color + mixer. PyAV's API is lower-level than the ffmpeg CLI, so concat, filter graphs, and LUT application all need manual graph construction.

**Benefit**: Same 1.5–6s per render as the Rust option. Same 0.1–1% improvement.

**Verdict**: No. Same reason. PyAV also has a habit of silently decoding frames into Python memory, which could regress memory use if we're not careful.

---

## Alternative 3 — Move the whole pipeline to TypeScript / Node

**Idea**: the web dashboard is already Node. Why not unify?

**Cost**: Enormous. We'd lose:
- `librosa` — no real equivalent in Node for audio feature extraction
- `madmom` — no port
- `scenedetect` — could be reimplemented but is OpenCV-backed
- `open_clip_torch` — Node has ONNX runtime but loading CLIP is painful
- `demucs`, `whisper`, `face_recognition` — all Python-first

We'd gain: one language. That's it.

**Verdict**: No. The ML ecosystem lives in Python. Node is strictly worse for this workload.

---

## Alternative 4 — Go for orchestration, keep Python for ML

**Idea**: the autopilot DAG is just orchestration. Rewrite autopilot.py in Go, keep the ML modules as Python subprocesses called from Go.

**Cost**: 1 week to rewrite autopilot as a Go binary with the same DAG.

**Benefit**: Faster autopilot startup (~0s vs ~2s), better concurrency primitives (if we ever want to parallelize steps), static binary deployment.

**Verdict**: Not worth it until the DAG grows beyond ~20 steps or we want true parallel stage execution. Today's 12-step sequential DAG doesn't benefit meaningfully. Also: the dev loop cost of crossing a language boundary every time we add a step is real.

Revisit if:
- We add parallel render queues (multiple projects at once)
- We add a long-running daemon mode (instead of one-shot CLI)
- Startup cost becomes user-visible (right now it's hidden behind the LLM and ffmpeg time)

---

## What we DO fix inside Python

Three things actually pay off, all targeted:

### 1. Kill subprocess recursion in autopilot (Phase D)

`autopilot.py::_run_subproc()` currently shells `ff beat`, `ff ingest`, `ff roughcut` as separate Python processes. Each fresh interpreter re-imports torch (2s), librosa (0.5s), madmom RNN model load (~1s on cold cache).

Total import overhead: ~20–40s per autopilot run.

Fix: call the underlying module functions directly. `step_beat_analyze` imports `fandomforge.audio.beat.analyze_song()` and calls it in-process. Same result, zero subprocess cost.

Keep `FF_AUTOPILOT_SUBPROCESS=1` as an escape hatch for debugging or step isolation.

### 2. Threadpool webhooks (Phase D)

`integrations/webhooks.py` uses `urllib.request.urlopen(timeout=10)` in a blocking call. On a flaky network, a single slow webhook stalls the whole autopilot.

Fix: dispatch each webhook in a small `ThreadPoolExecutor`, fire-and-forget with a 10s timeout. No retry complexity, same HMAC signing, same payload format.

### 3. Numpy-vectorize the color LUT build (Phase D)

`intelligence/color_matcher.py` lines 174–176 have a 17³ nested Python loop building a LUT. Current: ~100ms. Vectorized: ~5ms. Irrelevant in wall-time but it's the kind of code that attracts copy-paste and compounds over time. Fix it.

---

## What we're explicitly NOT touching

- **madmom's numpy-2 shim** — ugly but works, and the upstream fix is stalled. Leave the shim until madmom cuts a release.
- **The subprocess pattern for the actual ffmpeg calls** — ffmpeg as a subprocess is the industry standard and our error handling around it is already solid.
- **Switching from librosa to essentia or aubio** — librosa is well-tested for our use case, alternatives don't improve enough to justify retesting every beat-map.

---

## Revisit triggers

This decision holds until one of these becomes true:

1. Autopilot wall time is 50%+ Python overhead (currently <10%) — run the in-process refactor first
2. We add concurrent multi-project rendering (would justify Go orchestration)
3. A key ML dependency becomes Rust-native and well-maintained (realistic for whisper via `whisper.cpp`, unlikely for the others)
4. The web dashboard grows a real-time backend that Node would serve better than Python

Until then: Python, tuned in place.
