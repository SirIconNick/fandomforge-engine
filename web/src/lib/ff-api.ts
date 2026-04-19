/**
 * Thin wrapper around the `ff` CLI. All long-running CLI calls run as
 * subprocesses and stream their stdout back via SSE. Short synchronous calls
 * (validate, parse) run with a timeout and return the captured output.
 *
 * The dashboard must never assume CLI paths — we resolve once at startup from
 * env FF_CLI (default 'ff').
 */

import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "./fs";

const FF_CLI = process.env.FF_CLI ?? "ff";

export interface FFResult {
  ok: boolean;
  exitCode: number;
  stdout: string;
  stderr: string;
}

export async function runFF(
  args: string[],
  opts: { cwd?: string; timeoutMs?: number; env?: Record<string, string> } = {}
): Promise<FFResult> {
  const cwd = opts.cwd ?? PROJECT_ROOT;
  const timeoutMs = opts.timeoutMs ?? 120_000;
  return new Promise((resolve) => {
    const child = spawn(FF_CLI, args, {
      cwd,
      env: { ...process.env, ...(opts.env ?? {}) },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      stderr += `\n[ff-api] timed out after ${timeoutMs}ms`;
    }, timeoutMs);
    child.stdout.on("data", (b) => (stdout += b.toString("utf8")));
    child.stderr.on("data", (b) => (stderr += b.toString("utf8")));
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({
        ok: code === 0,
        exitCode: code ?? 1,
        stdout,
        stderr,
      });
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      resolve({
        ok: false,
        exitCode: 127,
        stdout,
        stderr: `${stderr}\nspawn error: ${err.message}`,
      });
    });
  });
}

export function projectDir(slug: string): string {
  return path.join(PROJECT_ROOT, "projects", slug);
}

export function projectDataPath(slug: string, artifact: string): string {
  return path.join(projectDir(slug), "data", artifact);
}
