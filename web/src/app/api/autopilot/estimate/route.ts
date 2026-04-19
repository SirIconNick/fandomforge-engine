import { NextRequest, NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const project = url.searchParams.get("project");
  if (!project) {
    return NextResponse.json({ error: "project param required" }, { status: 400 });
  }
  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");
  const proc = spawn(ffBinary, ["autopilot", "--project", project, "--estimate"], {
    cwd: PROJECT_ROOT,
    env: { ...process.env, PATH: `${path.dirname(ffBinary)}:${process.env.PATH ?? ""}` },
  });
  let out = "";
  let err = "";
  proc.stdout.on("data", (c: Buffer) => { out += c.toString("utf8"); });
  proc.stderr.on("data", (c: Buffer) => { err += c.toString("utf8"); });
  const exitCode: number = await new Promise((resolve) => {
    proc.on("exit", (code) => resolve(code ?? -1));
    proc.on("error", () => resolve(-1));
  });
  if (exitCode !== 0) {
    return NextResponse.json({ error: "estimate failed", stderr: err.slice(-400) }, { status: 500 });
  }
  try {
    const jsonStart = out.indexOf("{");
    const parsed = JSON.parse(jsonStart >= 0 ? out.slice(jsonStart) : out);
    return NextResponse.json(parsed);
  } catch (e) {
    return NextResponse.json({ error: "failed to parse estimate", stdout: out.slice(0, 400) }, { status: 500 });
  }
}
