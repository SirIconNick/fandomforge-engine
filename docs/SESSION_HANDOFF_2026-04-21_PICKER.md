# Session handoff — 2026-04-21 shot-picker rebuild

## What got done

Six-letter fix plan for the shot picker, all landed:

- **A** (commit fff5c28) — `densify_shot_list` respects `target_duration_sec`. Slot shots past target dropped, final kept shot clamped, tail fill terminates at target. `qa.duration` grades against target when shorter than song.
- **B** (commit 4d1b3cb) — `MIN_SCENE_LUMA` (started 0.15, later tightened to 0.22) hard-rejects dark scenes in `_pick_scene` with brightest-dark last-resort fallback.
- **C** (commit e702c82) — New `scene_enricher` module + `ff scenes enrich` CLI + `step_enrich_scenes` autopilot step. Backfills `avg_luma` + `peak_luma` on every scene (idempotent).
- **D** (commit 8c11001) — Shot-count budget governor. `target_cpm` by edit_type, `stretch_factor` on filler durations, clamp to `[band_lo, band_hi*1.5]`.
- **E** (commit 4a928a9) — Luma continuity. `_pick_scene` ranks candidates by luma proximity to the flanking shot. `last_picked_luma` tracked across fillers.
- **F** (commit b2a25a2) — Motion-direction continuity. Enricher now computes `motion_dir` per scene via frame-diff centroid analysis. Soft +0.3 penalty on opposite-axis directions in the picker.

Plus three cleanup fixes (commit 31e0d17):
- assembly: inverse `<stem>` → `fight_<stem>` lookup for raw/ resolution
- `MIN_SCENE_LUMA` raised to 0.22 to buffer against reviewer's 0.1 blackdetect threshold
- `_make_filler` caps filler duration to picked scene length (prevents extraction overrun into next scene)
- stale densify warnings cleaned on re-run

826 pytest green. 18 new tests across test_densify, test_shot_proposer, test_scene_enricher, test_qa_gate.

## Centuries-action render — before/after

| metric | before | after |
|---|---|---|
| duration | 229s | 90.0s |
| shot count | 297 | 63 |
| cpm | 78 | 42 |
| dark runtime | heavy (visual dim 0) | 0.67s / 90s (0.7%) |
| technical | pass | pass 100 |
| visual | ~0 | warn 57 |
| audio | pass | pass 100 |
| structural | pass | pass 100 |
| coherence | (not computed) | pass 100 |
| overall grade | D+ / 68 | B- / 80.3 |

Latest good render: `projects/centuries-action/exports/roughcut-v4.mp4`

## What still warns

Eye these next session if time allows, in rough priority order:

1. **Engagement = 63 — source dominance.** The rotation is use-count-based, not hard round-robin. One source can still dominate. Investigation path: look at actual per-source shot count in centuries-action/data/shot-list.json, see if source_use_count accumulates unevenly.
2. **Visual = 57 — 3 small dark segments.** One is a structural 0.21s fade-in at t=0. The other two are edge cases where scene's avg_luma is right at 0.22 but peak_luma is lower. Could add a `peak_luma >= 0.3` secondary filter, or sample more frames per scene at enrich time.
3. **Arc_shape = 75.** Pre-existing. Unrelated to picker. Arc-architect's rising-tension targets aren't being satisfied by the shot list — may need tension-curve → shot-list back-reference in the proposer.
4. **Shot_list = 69.** The densify warning stamps a string reviewers penalize. Trivial fix: move to metadata field or suppress when densify_count_ratio is sane.

## What this session did NOT fix

Quoted from the approved plan:

- Arc-architect classifying too many sections as "frantic." Budget governor masks it; doesn't cure it.
- Real shot-quality scoring (sharpness, composition, face presence). Still duration-heuristic for intensity.
- Emotional arc alignment. Shot picker is still oblivious to `emotion_arc.json`.
- Better transitions between acts.

## How to continue

### Quick smoke test
```bash
cd /Users/damato/Projects/fandomforge-engine
source tools/.venv/bin/activate
python -m fandomforge.cli review --project centuries-action --video roughcut-v4.mp4 --no-save
# expect B-/80.3
```

### Re-densify + re-render centuries-action (if source data changed)
```bash
python -m fandomforge.cli scenes enrich projects/centuries-action
python - <<'PY'
import json
from pathlib import Path
from fandomforge.autopilot import AutopilotContext, step_densify_shot_list
proj = Path("projects/centuries-action")
sl = json.loads((proj / "data" / "shot-list.json").read_text())
sl["shots"] = [s for s in sl["shots"] if not s.get("densified")]
sl["warnings"] = []
(proj / "data" / "shot-list.json").write_text(json.dumps(sl, indent=2))
ctx = AutopilotContext(
    project_slug="centuries-action", project_dir=proj,
    run_id="resession", song_path=None, source_glob=None, prompt="",
)
print(step_densify_shot_list(ctx))
PY
python -m fandomforge.cli roughcut --project centuries-action --song song.mp3 --output roughcut-vN.mp4
python -m fandomforge.cli review --project centuries-action --video roughcut-vN.mp4 --no-save
```

### To see the full picker logic
Read `docs/SHOT_PICKER_LOGIC.md`. Every step is documented with the
exact function it lives in.

### Postmortem cross-ref
`docs/RENDER_POSTMORTEM.md` has been updated to mark the target_duration,
dark-scene, machine-gun, flash-cut, filler-overrun, and fight_-lookup
items as FIXED with commit sha references.

## Files changed this session

- `tools/fandomforge/intelligence/shot_proposer.py` — bulk of the picker changes
- `tools/fandomforge/intelligence/scene_enricher.py` — new module
- `tools/fandomforge/autopilot.py` — step_enrich_scenes, target_duration_sec wiring
- `tools/fandomforge/cli.py` — ff scenes enrich command
- `tools/fandomforge/assembly/assemble.py` — inverse fight_ lookup
- `tools/fandomforge/qa/rules/duration.py` — grades against target
- `tools/tests/test_densify.py` — 12 new tests
- `tools/tests/test_scene_enricher.py` — new file, 10 tests
- `tools/tests/test_qa_gate.py` — 1 new test
- `docs/RENDER_POSTMORTEM.md` — updated 6 entries to FIXED status
- `docs/SHOT_PICKER_LOGIC.md` — new, authoritative picker reference
- `docs/SESSION_HANDOFF_2026-04-21_PICKER.md` — this file
