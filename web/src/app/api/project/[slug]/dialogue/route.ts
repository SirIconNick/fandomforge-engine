import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

type Params = Promise<{ slug: string }>;

export async function GET(_req: Request, { params }: { params: Params }) {
  const { slug } = await params;
  const base = path.join(PROJECT_ROOT, "projects", slug);
  const candidates = [
    path.join(base, "data", "dialogue-script.json"),
    path.join(base, "dialogue-script.json"),
  ];
  for (const jsonPath of candidates) {
    try {
      const text = await fs.readFile(jsonPath, "utf8");
      return NextResponse.json(JSON.parse(text));
    } catch {
      // try next
    }
  }
  return NextResponse.json({ cues: [] });
}
