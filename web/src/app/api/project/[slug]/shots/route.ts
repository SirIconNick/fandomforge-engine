import { NextResponse } from "next/server";
import path from "node:path";
import { spawn } from "node:child_process";
import { PROJECT_ROOT } from "@/lib/fs";

type Params = Promise<{ slug: string }>;

function runFf(args: string[]): Promise<{ stdout: string; code: number }> {
  return new Promise((resolve) => {
    const ff = process.env.FF_BINARY || path.join(PROJECT_ROOT, ".venv/bin/ff");
    const proc = spawn(ff, args, { cwd: PROJECT_ROOT });
    let stdout = "";
    proc.stdout.on("data", (c) => (stdout += c.toString()));
    proc.stderr.on("data", (c) => (stdout += c.toString()));
    proc.on("close", (code) => resolve({ stdout, code: code ?? -1 }));
    proc.on("error", () => resolve({ stdout, code: -1 }));
  });
}

export async function GET(_req: Request, { params }: { params: Params }) {
  const { slug } = await params;
  const url = new URL(_req.url);
  const file = url.searchParams.get("file") ?? "shot-list.md";

  // Emit JSON to a temp file via CLI and read it back
  const tempName = `/tmp/ff_shots_${slug}_${Date.now()}.json`;
  const { code } = await runFf([
    "shots",
    "parse",
    "--project",
    slug,
    "--file",
    file,
    "-o",
    tempName,
  ]);

  if (code !== 0) {
    return NextResponse.json(
      { error: `Failed to parse shots (ff exit ${code})` },
      { status: 500 },
    );
  }

  const fs = await import("node:fs/promises");
  try {
    const text = await fs.readFile(tempName, "utf8");
    const data = JSON.parse(text);
    await fs.unlink(tempName).catch(() => {});
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: `Could not read shot JSON: ${String(err)}` },
      { status: 500 },
    );
  }
}
