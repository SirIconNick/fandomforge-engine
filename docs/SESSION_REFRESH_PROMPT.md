# Session refresh prompt

Copy-paste this into a fresh Claude Code session (after `/clear` or on a new
machine) to pick up where the last session ended without losing context.
Current as of commit `7101d32` — 2026-04-20 session end.

---

Picking up FandomForge where the last session left off. Read first in order:

1. `docs/RENDER_POSTMORTEM.md` — 7 gotchas with root causes + forward-looking guards
2. `docs/SESSION_HANDOFF_2026-04-21.md` — engine state snapshot
3. `docs/ORCHESTRATOR_ACTIVATION.md` — daemon start/stop commands (daemon is NOT running)
4. `~/Documents/Obsidian Vault/Projects/FandomForge Engine — State & Gaps.md` — what works / what doesn't
5. `git log --oneline | head -20` — last 20 commits

**Current state:** action-legends reviews A-/91.3 under the new arc_shape rubric. Fresh-autopilot on `projects/centuries-action/` renders but grades D+/68 with ACTUAL quality issues (blank-frame segments, machine-gun 0.77s cutting, ignored 90s target). The D+/68 is polite; the actual watched cut is trash — see the honest-reality entry in RENDER_POSTMORTEM.md.

**This session's top priorities — in order, no scope drift:**

1. **Luma-aware scene picker** — extend `shot_proposer.densify_shot_list._pick_scene` to read `avg_luma` from scene JSON and deprioritize scenes where `avg_luma < 0.15`. Derived scenes from `ff ingest` don't carry luma — either (a) add luma sampling to `intelligence/reference_analyzer_deep._scene_boundaries` at ingest time, or (b) on-the-fly probe in densify. Prefer (a) so it's computed once and every downstream gets it.

2. **Respect target_duration_sec** — when `project-config.target_duration_sec < beat_map.duration_sec`, the engine must trim. Either (i) slice beat-map's sync_points to [0, target_duration], (ii) generate a trimmed song file in assets/ at target length, or (iii) in densify, clamp shot timeline to `min(target_duration, song_duration)`. Recommend (iii) as least-invasive.

3. **Reduce filler density** — densify currently aims for 50 cpm but lands at 74 cpm because fillers take the lower bound of pacing bands. Change `_filler_dur_sec` to use the MEDIAN of the band instead of the short end. If (target_duration / sync_point_count) > band_median, ADD MORE fillers; if <, use fewer longer ones. Result: closer to 50 cpm.

4. **Basic continuity pass** — when picking a filler, prefer scenes whose `avg_luma` is within ±0.2 of the flanking shot's luma (prevents flash cuts between a black scene and a bright one). Cheap, high-impact.

**Verification target:** re-render centuries-action, expect grade ≥ B/85. If engagement dim > 85 and visual dim > 70, ship it as the new baseline.

**Don't:**
- Auto-stamp fair_use blanket statements (case-by-case only per CLAUDE.md).
- Re-enable qa.copyright as a blocker.
- Chase other phases until the four items above land.

**Orchestrator:** not running. Queue of 23 tasks is seeded at `references/orchestrator-queue.json`. Start with `./scripts/orchestrator-start.sh` if Nick wants autonomous work on side. The luma + duration + filler fixes should be done in the FOREGROUND first — they're more important than the queued whisper runs.

**Current project to render against:** `projects/centuries-action/` — centuries.mp3 + 10 action clips symlinked from action-legends/raw/ + /raw/fights/. project-config targets 90s action. Use this as the A-grade-target proving ground.

**Pre-flight before re-render:**
```
cd /Users/damato/Projects/fandomforge-engine
rm -f projects/centuries-action/data/{shot-list.json,sync-plan.json,emotion-arc.json,complement-plan.json,qa-report.json,aspect-plan.json,sfx-plan.json,post-render-review.json} projects/centuries-action/shot-list.md
rm -rf projects/centuries-action/exports
FF_REFERENCES_DIR=$(pwd)/references \
PATH="$(pwd)/tools/.venv/bin:$PATH" \
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 TORCH_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2 \
./tools/.venv/bin/ff autopilot --project centuries-action --song projects/centuries-action/assets/song.mp3 --prompt "Action legends — bullet choreography, hand-to-hand carnage. Centuries by Fall Out Boy. Hard cuts on every drop."
```

**When fixes land, the honest grade is:** watch `projects/centuries-action/exports/graded.mp4` directly. If it looks like trash to a human eye, it IS trash regardless of what the rubric says. Add dimensions to the rubric that penalize what's actually broken.

---

## Watch list — things to also verify each session

- Load average via `uptime` — if >5, hold off on renders
- Git tree clean — always start from clean state
- `./tools/.venv/bin/ff --version` works — venv intact
- `references/priors/` has 5 bucket files — per-bucket priors loaded
- action-legends `graded.mp4` still reviews at A-/91.3 — baseline preserved
