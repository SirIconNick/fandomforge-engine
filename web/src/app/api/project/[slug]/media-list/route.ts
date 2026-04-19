import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

type Params = Promise<{ slug: string }>;

async function listFilesRecursive(dir: string, base = ""): Promise<string[]> {
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    const out: string[] = [];
    for (const entry of entries) {
      const rel = base ? `${base}/${entry.name}` : entry.name;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name.startsWith(".")) continue;
        out.push(...(await listFilesRecursive(full, rel)));
      } else {
        out.push(rel);
      }
    }
    return out;
  } catch {
    return [];
  }
}

export async function GET(_req: Request, { params }: { params: Params }) {
  const { slug } = await params;
  const projectDir = path.join(PROJECT_ROOT, "projects", slug);

  const [exports, raw, dialogue, selects] = await Promise.all([
    listFilesRecursive(path.join(projectDir, "exports")),
    listFilesRecursive(path.join(projectDir, "raw")),
    listFilesRecursive(path.join(projectDir, "dialogue")),
    listFilesRecursive(path.join(projectDir, "selects")),
  ]);

  return NextResponse.json({
    project: slug,
    exports,
    raw,
    dialogue,
    selects,
  });
}
