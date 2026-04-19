import { NextRequest, NextResponse } from "next/server";
import path from "node:path";
import { promises as fs } from "node:fs";
import { PROJECT_ROOT } from "@/lib/fs";
import { runFF } from "@/lib/ff-api";

interface RenderRequest {
  output?: string;
  color_plan?: string | null;
}

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

  let body: RenderRequest = {};
  try {
    body = (await req.json()) as RenderRequest;
  } catch {
    /* empty body is fine */
  }

  const output = body.output ?? "roughcut.mp4";
  const args = ["roughcut", "--project", slug, "--output", output];
  if (body.color_plan) {
    args.push("--color-plan", body.color_plan);
  }

  // Roughcut can take 15+ minutes on long projects. Bump timeout hard.
  const res = await runFF(args, { timeoutMs: 30 * 60_000 });

  const output_path = path.join(projDir, "exports", output);
  let bytes = 0;
  try {
    bytes = (await fs.stat(output_path)).size;
  } catch {
    /* not produced */
  }

  return NextResponse.json({
    ok: res.ok && bytes > 0,
    exitCode: res.exitCode,
    stdout: res.stdout.slice(-4000),
    stderr: res.stderr.slice(-4000),
    output_path,
    bytes,
  });
}
