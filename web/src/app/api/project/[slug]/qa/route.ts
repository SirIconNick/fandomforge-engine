import { NextRequest, NextResponse } from "next/server";
import path from "node:path";
import { promises as fs } from "node:fs";
import { PROJECT_ROOT } from "@/lib/fs";
import { runFF } from "@/lib/ff-api";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  const projDir = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projDir);
  } catch {
    return NextResponse.json({ error: "project not found" }, { status: 404 });
  }

  let body: { overrides?: Record<string, string> } = {};
  try {
    body = (await req.json()) as { overrides?: Record<string, string> };
  } catch {
    // empty body is fine
  }

  const args = ["qa", "gate", "--project", projDir];
  for (const [k, v] of Object.entries(body.overrides ?? {})) {
    if (!v) continue;
    args.push("--override", `${k}=${v}`);
  }
  const res = await runFF(args, { timeoutMs: 60_000 });

  let report: unknown = null;
  const reportPath = path.join(projDir, "data", "qa-report.json");
  try {
    report = JSON.parse(await fs.readFile(reportPath, "utf8"));
  } catch {
    // report wasn't produced; stdout/stderr will tell the story
  }

  return NextResponse.json({
    ok: res.ok,
    exitCode: res.exitCode,
    stdout: res.stdout,
    stderr: res.stderr,
    report,
  });
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  const reportPath = path.join(PROJECT_ROOT, "projects", slug, "data", "qa-report.json");
  try {
    const text = await fs.readFile(reportPath, "utf8");
    return NextResponse.json(JSON.parse(text));
  } catch {
    return NextResponse.json({ error: "qa-report.json not found; run gate first" }, { status: 404 });
  }
}
