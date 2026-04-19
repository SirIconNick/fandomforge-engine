import { NextRequest, NextResponse } from "next/server";
import path from "node:path";
import { promises as fs } from "node:fs";
import { PROJECT_ROOT } from "@/lib/fs";
import { runFF } from "@/lib/ff-api";

function reviewPath(slug: string): string {
  return path.join(PROJECT_ROOT, "projects", slug, "data", "post-render-review.json");
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  try {
    const text = await fs.readFile(reviewPath(slug), "utf8");
    return NextResponse.json(JSON.parse(text));
  } catch {
    return NextResponse.json(
      { error: "post-render-review.json not found; run `ff review` or autopilot first" },
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

  const res = await runFF(["review", "--project", slug, "--save"], {
    timeoutMs: 5 * 60_000,
  });

  let report: unknown = null;
  try {
    report = JSON.parse(await fs.readFile(reviewPath(slug), "utf8"));
  } catch {
    // review command didn't produce output (render missing, etc.)
  }

  return NextResponse.json({
    ok: res.ok,
    exitCode: res.exitCode,
    stdout: res.stdout,
    stderr: res.stderr,
    report,
  });
}
