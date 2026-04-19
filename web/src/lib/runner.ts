import { spawn } from "node:child_process";
import path from "node:path";
import fs from "node:fs";
import { PROJECT_ROOT } from "@/lib/fs";

export interface PipelineRun {
  id: string;
  project: string;
  command: string;
  args: string[];
  logPath: string;
  startedAt: string;
  status: "running" | "completed" | "failed";
  exitCode?: number;
  finishedAt?: string;
}

const runs = new Map<string, PipelineRun>();
const RUNS_DIR = path.join(PROJECT_ROOT, ".runs");

function ensureRunsDir() {
  if (!fs.existsSync(RUNS_DIR)) {
    fs.mkdirSync(RUNS_DIR, { recursive: true });
  }
}

function newRunId(): string {
  return `run_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function listRuns(): PipelineRun[] {
  return Array.from(runs.values()).sort((a, b) =>
    b.startedAt.localeCompare(a.startedAt),
  );
}

export function getRun(id: string): PipelineRun | undefined {
  return runs.get(id);
}

export function startPipelineRun({
  project,
  shotList,
  song,
  dialogue,
  colorPlan,
  colorPreset,
  output,
  width,
  height,
  fps,
  songOffset,
}: {
  project: string;
  shotList?: string;
  song?: string;
  dialogue?: string;
  colorPlan?: string;
  colorPreset?: string;
  output?: string;
  width?: number;
  height?: number;
  fps?: number;
  songOffset?: number;
}): PipelineRun {
  ensureRunsDir();

  const id = newRunId();
  const logPath = path.join(RUNS_DIR, `${id}.log`);
  const logFd = fs.openSync(logPath, "w");

  const ffBinary = process.env.FF_BINARY || path.join(PROJECT_ROOT, ".venv/bin/ff");
  const args: string[] = ["roughcut", "--project", project];

  if (shotList) args.push("--shot-list", shotList);
  if (song) args.push("--song", song);
  if (dialogue) args.push("--dialogue", dialogue);
  if (colorPlan) args.push("--color-plan", colorPlan);
  if (colorPreset) args.push("--color", colorPreset);
  if (output) args.push("--output", output);
  if (width) args.push("--width", String(width));
  if (height) args.push("--height", String(height));
  if (fps) args.push("--fps", String(fps));
  if (songOffset !== undefined) args.push("--song-offset", String(songOffset));

  const proc = spawn(ffBinary, args, {
    cwd: PROJECT_ROOT,
    stdio: ["ignore", logFd, logFd],
    env: { ...process.env, PATH: `${path.dirname(ffBinary)}:${process.env.PATH}` },
  });

  const run: PipelineRun = {
    id,
    project,
    command: ffBinary,
    args,
    logPath,
    startedAt: new Date().toISOString(),
    status: "running",
  };
  runs.set(id, run);

  proc.on("exit", (code) => {
    const updated = runs.get(id);
    if (updated) {
      updated.exitCode = code ?? -1;
      updated.finishedAt = new Date().toISOString();
      updated.status = code === 0 ? "completed" : "failed";
    }
    try {
      fs.closeSync(logFd);
    } catch {}
  });

  proc.on("error", () => {
    const updated = runs.get(id);
    if (updated) {
      updated.status = "failed";
      updated.finishedAt = new Date().toISOString();
    }
  });

  return run;
}

export async function* streamRunLog(id: string) {
  const run = getRun(id);
  if (!run) return;
  const { logPath } = run;

  let offset = 0;
  while (true) {
    if (!fs.existsSync(logPath)) {
      await new Promise((r) => setTimeout(r, 200));
      continue;
    }
    const stat = fs.statSync(logPath);
    if (stat.size > offset) {
      const fd = fs.openSync(logPath, "r");
      const buf = Buffer.alloc(stat.size - offset);
      fs.readSync(fd, buf, 0, buf.length, offset);
      fs.closeSync(fd);
      offset = stat.size;
      yield buf.toString("utf8");
    }
    const current = getRun(id);
    if (current && current.status !== "running") {
      // Flush any last bytes then stop
      const finalStat = fs.statSync(logPath);
      if (finalStat.size > offset) {
        const fd = fs.openSync(logPath, "r");
        const buf = Buffer.alloc(finalStat.size - offset);
        fs.readSync(fd, buf, 0, buf.length, offset);
        fs.closeSync(fd);
        yield buf.toString("utf8");
      }
      return;
    }
    await new Promise((r) => setTimeout(r, 300));
  }
}
