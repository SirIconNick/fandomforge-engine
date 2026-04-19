import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

const SCHEMAS_DIR = path.join(PROJECT_ROOT, "tools", "fandomforge", "schemas");

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const artifact = url.searchParams.get("artifact");
  if (!artifact) {
    return NextResponse.json({ error: "artifact param required" }, { status: 400 });
  }
  // Guard against path traversal — only allow simple [a-z0-9-] names.
  if (!/^[a-z0-9-]+$/.test(artifact)) {
    return NextResponse.json({ error: "invalid artifact id" }, { status: 400 });
  }
  const p = path.join(SCHEMAS_DIR, `${artifact}.schema.json`);
  try {
    const raw = await fs.readFile(p, "utf8");
    const schema = JSON.parse(raw);
    return NextResponse.json({ ok: true, artifact, schema });
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return NextResponse.json(
        { error: `no schema for artifact '${artifact}'` },
        { status: 404 }
      );
    }
    return NextResponse.json(
      { error: (err as Error).message },
      { status: 500 }
    );
  }
}
