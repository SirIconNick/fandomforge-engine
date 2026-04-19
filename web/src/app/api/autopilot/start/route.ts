import { NextRequest, NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import { projectExists } from "@/lib/project-context";

interface StartRequest {
  project_slug: string;
  prompt?: string;
}

export async function POST(req: NextRequest) {
  let body: StartRequest;
  try {
    body = (await req.json()) as StartRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }
  if (!body.project_slug) {
    return NextResponse.json({ error: "project_slug required" }, { status: 400 });
  }
  if (!(await projectExists(body.project_slug))) {
    return NextResponse.json({ error: "project not found" }, { status: 404 });
  }

  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");

  const args = ["autopilot", "--project", body.project_slug];
  if (body.prompt) args.push("--prompt", body.prompt);

  const proc = spawn(ffBinary, args, {
    cwd: PROJECT_ROOT,
    detached: true,
    stdio: "ignore",
    env: { ...process.env, PATH: `${path.dirname(ffBinary)}:${process.env.PATH ?? ""}` },
  });
  proc.unref();

  const runId = `run_${Date.now()}_${proc.pid ?? Math.floor(Math.random() * 10000)}`;
  return NextResponse.json({ ok: true, run_id: runId, pid: proc.pid ?? 0 });
}
