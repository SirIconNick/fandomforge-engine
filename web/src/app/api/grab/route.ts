import { NextRequest, NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import { projectExists } from "@/lib/project-context";

interface GrabRequest {
  project_slug: string;
  url: string;
  kind: "video" | "song";
  license_note?: string;
  resolution?: string;
  filename?: string;
}

export async function POST(req: NextRequest) {
  let body: GrabRequest;
  try {
    body = (await req.json()) as GrabRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!body.project_slug || !body.url || !body.kind) {
    return NextResponse.json(
      { error: "project_slug, url, and kind are required" },
      { status: 400 }
    );
  }
  if (body.kind !== "video" && body.kind !== "song") {
    return NextResponse.json(
      { error: "kind must be 'video' or 'song'" },
      { status: 400 }
    );
  }
  if (!(await projectExists(body.project_slug))) {
    return NextResponse.json({ error: "project not found" }, { status: 404 });
  }

  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");

  const args = ["grab", body.kind, "--project", body.project_slug, "--url", body.url];
  if (body.license_note) args.push("--license-note", body.license_note);
  if (body.kind === "video") {
    if (body.resolution) args.push("--resolution", body.resolution);
    if (body.filename) args.push("--filename", body.filename);
    args.push("--no-ingest");
  } else if (body.filename) {
    args.push("--filename", body.filename);
  }

  const proc = spawn(ffBinary, args, {
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      PATH: `${path.dirname(ffBinary)}:${process.env.PATH ?? ""}`,
    },
  });

  let stdout = "";
  let stderr = "";
  proc.stdout.on("data", (c: Buffer) => { stdout += c.toString("utf8"); });
  proc.stderr.on("data", (c: Buffer) => { stderr += c.toString("utf8"); });

  const exitCode: number = await new Promise((resolve) => {
    proc.on("exit", (code) => resolve(code ?? -1));
    proc.on("error", () => resolve(-1));
  });

  if (exitCode !== 0) {
    return NextResponse.json(
      {
        error: "grab failed",
        exit_code: exitCode,
        stdout: stdout.slice(-1200),
        stderr: stderr.slice(-800),
      },
      { status: 500 }
    );
  }

  return NextResponse.json({
    ok: true,
    exit_code: exitCode,
    log: (stdout + stderr).slice(-1600),
  });
}
