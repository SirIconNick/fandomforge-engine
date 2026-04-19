import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

type Params = Promise<{ slug: string; parts: string[] }>;

const ALLOWED_EXT = new Set([".mp4", ".mov", ".mkv", ".webm"]);

export async function GET(req: Request, { params }: { params: Params }) {
  const { slug, parts } = await params;

  const relative = parts.join("/");
  const projectDir = path.join(PROJECT_ROOT, "projects", slug);
  const fullPath = path.resolve(projectDir, relative);

  if (!fullPath.startsWith(projectDir)) {
    return NextResponse.json({ error: "Invalid path" }, { status: 400 });
  }
  if (!ALLOWED_EXT.has(path.extname(fullPath).toLowerCase())) {
    return NextResponse.json({ error: "Not a video file" }, { status: 400 });
  }
  if (!fs.existsSync(fullPath)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const stat = fs.statSync(fullPath);
  const size = stat.size;
  const range = req.headers.get("range");

  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    if (!m) {
      return NextResponse.json({ error: "Invalid range" }, { status: 416 });
    }
    const start = m[1] ? parseInt(m[1], 10) : 0;
    const end = m[2] ? parseInt(m[2], 10) : size - 1;
    const chunkSize = end - start + 1;

    const file = fs.createReadStream(fullPath, { start, end });
    return new Response(file as unknown as ReadableStream, {
      status: 206,
      headers: {
        "Content-Range": `bytes ${start}-${end}/${size}`,
        "Accept-Ranges": "bytes",
        "Content-Length": String(chunkSize),
        "Content-Type": "video/mp4",
      },
    });
  }

  const file = fs.createReadStream(fullPath);
  return new Response(file as unknown as ReadableStream, {
    headers: {
      "Accept-Ranges": "bytes",
      "Content-Length": String(size),
      "Content-Type": "video/mp4",
    },
  });
}
