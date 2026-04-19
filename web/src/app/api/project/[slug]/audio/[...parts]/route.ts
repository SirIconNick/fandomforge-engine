import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

type Params = Promise<{ slug: string; parts: string[] }>;

const ALLOWED_EXT = new Set([".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"]);

const CONTENT_TYPES: Record<string, string> = {
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
  ".m4a": "audio/mp4",
  ".aac": "audio/aac",
  ".flac": "audio/flac",
  ".ogg": "audio/ogg",
};

export async function GET(req: Request, { params }: { params: Params }) {
  const { slug, parts } = await params;
  const relative = parts.join("/");
  const projectDir = path.join(PROJECT_ROOT, "projects", slug);
  const fullPath = path.resolve(projectDir, relative);

  if (!fullPath.startsWith(projectDir)) {
    return NextResponse.json({ error: "Invalid path" }, { status: 400 });
  }
  const ext = path.extname(fullPath).toLowerCase();
  if (!ALLOWED_EXT.has(ext)) {
    return NextResponse.json({ error: "Not an audio file" }, { status: 400 });
  }
  if (!fs.existsSync(fullPath)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const stat = fs.statSync(fullPath);
  const size = stat.size;
  const range = req.headers.get("range");
  const contentType = CONTENT_TYPES[ext] ?? "application/octet-stream";

  if (range) {
    const match = /bytes=(\d*)-(\d*)/.exec(range);
    if (match) {
      const start = match[1] ? parseInt(match[1], 10) : 0;
      const end = match[2] ? parseInt(match[2], 10) : size - 1;
      const chunkSize = end - start + 1;
      const file = fs.createReadStream(fullPath, { start, end });
      return new Response(file as unknown as ReadableStream, {
        status: 206,
        headers: {
          "Content-Range": `bytes ${start}-${end}/${size}`,
          "Accept-Ranges": "bytes",
          "Content-Length": String(chunkSize),
          "Content-Type": contentType,
        },
      });
    }
  }

  const file = fs.createReadStream(fullPath);
  return new Response(file as unknown as ReadableStream, {
    headers: {
      "Accept-Ranges": "bytes",
      "Content-Length": String(size),
      "Content-Type": contentType,
    },
  });
}
