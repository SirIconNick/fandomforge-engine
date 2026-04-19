import { NextRequest, NextResponse } from "next/server";
import path from "node:path";
import { promises as fs } from "node:fs";
import { PROJECT_ROOT } from "@/lib/fs";
import { runFF } from "@/lib/ff-api";

interface ExportRequest {
  format?: "fcpxml" | "edl" | "both";
  output_base?: string;
  audio_track?: string;
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

  let body: ExportRequest = {};
  try {
    body = (await req.json()) as ExportRequest;
  } catch {
    /* empty body is fine */
  }

  const format = body.format ?? "both";
  const output_base = body.output_base ?? "timeline";

  const args = [
    "export-nle",
    "--project", slug,
    "--format", format,
    "--output-base", output_base,
  ];
  if (body.audio_track) {
    args.push("--audio-track", body.audio_track);
  }
  const res = await runFF(args, { timeoutMs: 60_000 });

  const outputs: string[] = [];
  for (const ext of format === "both" ? ["fcpxml", "edl"] : [format]) {
    const p = path.join(projDir, "exports", `${output_base}.${ext}`);
    try {
      await fs.access(p);
      outputs.push(p);
    } catch {
      /* not produced */
    }
  }

  return NextResponse.json({
    ok: res.ok && outputs.length > 0,
    exitCode: res.exitCode,
    stdout: res.stdout.slice(-4000),
    stderr: res.stderr.slice(-4000),
    outputs,
  });
}
