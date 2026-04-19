import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  const projectDir = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projectDir);
  } catch {
    return NextResponse.json({ error: "project not found" }, { status: 404 });
  }

  const catalogPath = path.join(projectDir, "data", "source-catalog.json");
  let catalog: { sources?: Array<Record<string, unknown>> } = {};
  try {
    catalog = JSON.parse(await fs.readFile(catalogPath, "utf8"));
  } catch {
    // no catalog yet — fall back to raw dir listing
  }

  const result: {
    catalog_sources: unknown[];
    raw_files: Array<{ name: string; path: string; size: number; ext: string }>;
    dialogue_files: Array<{ name: string; path: string; size: number }>;
    sfx_files: Array<{ name: string; path: string; size: number }>;
    export_files: Array<{ name: string; path: string; size: number }>;
  } = {
    catalog_sources: catalog.sources ?? [],
    raw_files: await listDir(path.join(projectDir, "raw")),
    dialogue_files: await listDir(path.join(projectDir, "dialogue")),
    sfx_files: await listDir(path.join(projectDir, "sfx")),
    export_files: await listDir(path.join(projectDir, "exports"), { recursive: true }),
  };
  return NextResponse.json(result);
}

async function listDir(
  p: string,
  opts: { recursive?: boolean } = {}
): Promise<Array<{ name: string; path: string; size: number; ext: string }>> {
  try {
    const entries = await fs.readdir(p, { withFileTypes: true });
    const out: Array<{ name: string; path: string; size: number; ext: string }> = [];
    for (const e of entries) {
      const full = path.join(p, e.name);
      if (e.isDirectory()) {
        if (opts.recursive) {
          const nested = await listDir(full, opts);
          out.push(...nested);
        }
        continue;
      }
      const stat = await fs.stat(full).catch(() => null);
      if (!stat) continue;
      out.push({
        name: e.name,
        path: full,
        size: stat.size,
        ext: path.extname(e.name).toLowerCase(),
      });
    }
    return out;
  } catch {
    return [];
  }
}
