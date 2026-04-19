# Subagent Launch Log

Record of when expert agents were registered with Claude Code and what they're expected to be triggered by. Update this whenever the roster changes.

## 2026-04-18 — Initial launch (Phase A)

All 12 FandomForge experts were copied from `agents/` into `.claude/agents/` and registered with Claude Code. The `.claude/agents/` directory had existed as an empty folder since the project was first scaffolded; this is the first time it contains any actual agent definitions, meaning the experts were previously unreachable from the terminal.

### Installed agents

| Name | Color | Tools | Auto-trigger examples |
|---|---|---|---|
| edit-strategist | gold | Read, Write, Glob, Grep, WebSearch | "help me start a new edit", "my edit is stuck", "draft a master plan" |
| beat-mapper | red | Bash, Read, Write, Glob | "analyze this song", "find the drops", "build a beat map", anything BPM/tempo/timing |
| story-weaver | purple | Read, Write, Glob, WebSearch | "my edit feels hollow", "tie these fandoms together", "theme arc" |
| shot-curator | orange | Read, Write, Glob, Grep | "pick shots for this theme", "build a shot list", "rank these clip options" |
| color-grader | teal | Read, Write, Glob | "my sources look mismatched", "LUT", "color direction" |
| transition-architect | blue | Read, Write, Glob | "transitions feel choppy", "match cut", "cut language plan" |
| fandom-researcher | emerald | Read, Glob, Grep, WebSearch | "does X have a scene where...", "iconic moments in [fandom]", scene timestamps |
| editor-guide | cyan | Read, Glob, WebSearch | "how do I in Resolve/Premiere/CapCut/Vegas", NLE-specific howto |
| audio-producer | magenta | Read, Write, Glob | "pick a song", "mix feels weak", "SFX layers", loudness targets |
| title-designer | pink | Read, Write, Glob | "title card", "kinetic lyrics", font choice, on-screen text |
| pipeline-tuner | slate | Bash, Read, Write, Glob | "optimize before render", "why did output look over-processed", ffmpeg tuning |
| qa-reviewer | yellow | Bash, Read, Glob, Grep | "review the rough cut", "quality gate", "check for black frames / clipping" |
| shot-proposer | indigo | Bash, Read, Write, Glob | "draft a shot list", "scaffold shots from my edit plan", "give me a first pass" (added 2026-04-19, Phase K) |
| autopilot-orchestrator | navy | Bash, Read, Write, Glob | "just run the whole thing", "resume my autopilot", "render me a draft" (added 2026-04-19, Phase S) |

### Distribution rationale

Bash access is limited to the three experts that actually run CLI tools (beat-mapper, pipeline-tuner, qa-reviewer). Every other agent is research-and-advice-only. Write is denied to the four research/advisory roles (fandom-researcher, editor-guide, and implicitly the two Bash-runners that don't need to modify artifacts — qa-reviewer only reads) since they don't author artifacts.

### Known cross-agent handoffs

The edit-strategist is the orchestrator. In the terminal, the main Claude Code thread will invoke strategist first, then dispatch to specialists based on strategist's recommendations. Strategist does not invoke other agents directly (no Agent tool in its allowlist) — it describes what needs to happen and the main thread fires the next call.

### Verification

- `ls .claude/agents/` returns 12 files
- Each file has a `tools:` YAML block and parses cleanly
- Source `agents/*.md` is byte-identical to `.claude/agents/*.md` (confirmed via `diff -r`)
- `agents/README.md` documents the 12-agent roster and the copy-not-symlink install pattern
