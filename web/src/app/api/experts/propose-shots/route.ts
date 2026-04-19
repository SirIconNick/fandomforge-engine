import { NextRequest, NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { compare } from "fast-json-patch";
import { PROJECT_ROOT } from "@/lib/fs";
import { projectExists, readArtifact } from "@/lib/project-context";
import { validateArtifact } from "@/lib/schemas";

interface ProposeRequest {
  project_slug: string;
}

export async function POST(req: NextRequest) {
  let body: ProposeRequest;
  try {
    body = (await req.json()) as ProposeRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!body.project_slug) {
    return NextResponse.json({ error: "project_slug required" }, { status: 400 });
  }
  if (!(await projectExists(body.project_slug))) {
    return NextResponse.json(
      { error: `project not found: ${body.project_slug}` },
      { status: 404 }
    );
  }

  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");

  const proc = spawn(ffBinary, [
    "propose", "shots",
    "--project", body.project_slug,
    "--dry-run",
  ], {
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
        error: "ff propose shots failed",
        stderr: stderr.slice(-1000),
        stdout: stdout.slice(-500),
        exit_code: exitCode,
      },
      { status: 500 }
    );
  }

  let draft: unknown;
  try {
    const jsonStart = stdout.indexOf("{");
    const jsonText = jsonStart >= 0 ? stdout.slice(jsonStart) : stdout;
    draft = JSON.parse(jsonText);
  } catch (err) {
    return NextResponse.json(
      { error: `failed to parse proposer output: ${(err as Error).message}`, stdout: stdout.slice(0, 500) },
      { status: 500 }
    );
  }

  const validation = await validateArtifact("shot-list", draft);
  if (!validation.ok) {
    return NextResponse.json(
      { error: "proposer output failed schema validation", schema_errors: validation.errors },
      { status: 500 }
    );
  }

  const current = await readArtifact(body.project_slug, "shot-list");
  const starting = (current.data as object | null) ?? {};
  const patch = compare(starting, draft as object);

  return NextResponse.json({
    ok: true,
    draft_summary: {
      shot_count: Array.isArray((draft as { shots?: unknown[] }).shots)
        ? (draft as { shots: unknown[] }).shots.length
        : 0,
      generator: (draft as { generator?: string }).generator,
    },
    patch: {
      tool_use_id: `proposer_${Date.now()}`,
      expert_slug: "shot-proposer",
      artifact: "shot-list",
      rationale: "Heuristic draft from edit-plan + beat-map + catalog. Review per-op before applying.",
      patch,
    },
  });
}
