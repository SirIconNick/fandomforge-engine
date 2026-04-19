import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import { runFF } from "@/lib/ff-api";

/**
 * GET  /api/project/[slug]/shot-list  -> current shot-list.json
 * PUT  /api/project/[slug]/shot-list  -> replace shot-list.json
 *
 * PUT validates the payload through the CLI (`ff validate file`) so the
 * dashboard can't write a schema-invalid shot-list to disk.
 */

async function shotListPath(slug: string): Promise<string> {
  return path.join(PROJECT_ROOT, "projects", slug, "data", "shot-list.json");
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  const p = await shotListPath(slug);
  try {
    const text = await fs.readFile(p, "utf8");
    return NextResponse.json(JSON.parse(text));
  } catch {
    return NextResponse.json({ error: "shot-list.json not found" }, { status: 404 });
  }
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const p = await shotListPath(slug);
  await fs.mkdir(path.dirname(p), { recursive: true });

  // Write to a temp sibling and validate before swapping into place.
  const tempPath = `${p}.tmp-${Date.now()}`;
  await fs.writeFile(tempPath, JSON.stringify(body, null, 2), "utf8");

  const validation = await runFF(["validate", "file", tempPath, "--schema", "shot-list"], {
    timeoutMs: 20_000,
  });
  if (!validation.ok) {
    try {
      await fs.unlink(tempPath);
    } catch {
      // ignore
    }
    return NextResponse.json(
      { error: "Validation failed", stdout: validation.stdout, stderr: validation.stderr },
      { status: 422 }
    );
  }

  await fs.rename(tempPath, p);
  return NextResponse.json({ ok: true, path: p });
}
