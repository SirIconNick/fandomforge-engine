import { NextRequest, NextResponse } from "next/server";
import path from "node:path";
import { promises as fs } from "node:fs";
import { PROJECT_ROOT } from "@/lib/fs";
import { runFF } from "@/lib/ff-api";

function planPath(slug: string): string {
  return path.join(PROJECT_ROOT, "projects", slug, "data", "sync-plan.json");
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  try {
    const text = await fs.readFile(planPath(slug), "utf8");
    return NextResponse.json(JSON.parse(text));
  } catch {
    return NextResponse.json(
      { error: "sync-plan.json not found; run `ff sync plan` or autopilot" },
      { status: 404 }
    );
  }
}

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  const projDir = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projDir);
  } catch {
    return NextResponse.json({ error: "project not found" }, { status: 404 });
  }
  const res = await runFF(["sync", "plan", "--project", slug], {
    timeoutMs: 60_000,
  });
  let plan: unknown = null;
  try {
    plan = JSON.parse(await fs.readFile(planPath(slug), "utf8"));
  } catch {
    /* not produced */
  }
  return NextResponse.json({
    ok: res.ok && plan !== null,
    exitCode: res.exitCode,
    stdout: res.stdout.slice(-2000),
    stderr: res.stderr.slice(-2000),
    plan,
  });
}
