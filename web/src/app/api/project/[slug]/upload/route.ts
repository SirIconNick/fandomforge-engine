import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

const ALLOWED_EXTENSIONS = [
  ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi",
  ".mp3", ".wav", ".m4a", ".aac", ".flac",
  ".png", ".jpg", ".jpeg",
  ".srt", ".vtt",
  ".cube",
];

const MAX_BYTES = 8 * 1024 * 1024 * 1024; // 8 GB per file

function safeFilename(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 240);
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-z0-9][a-z0-9-_]*$/i.test(slug)) {
    return NextResponse.json({ error: "Invalid project slug" }, { status: 400 });
  }

  const projDir = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projDir);
  } catch {
    return NextResponse.json({ error: `Project '${slug}' does not exist. Create it first.` }, { status: 404 });
  }

  const contentType = req.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
    return NextResponse.json({ error: "Must be multipart/form-data" }, { status: 415 });
  }

  const form = await req.formData();
  const targetDirRaw = String(form.get("target_dir") ?? "raw").trim();
  const allowedTargets = new Set(["raw", "dialogue", "sfx", "references", "luts"]);
  if (!allowedTargets.has(targetDirRaw)) {
    return NextResponse.json(
      { error: `target_dir must be one of ${[...allowedTargets].join(", ")}` },
      { status: 400 }
    );
  }

  const targetDir = path.join(projDir, targetDirRaw);
  await fs.mkdir(targetDir, { recursive: true });

  const files = form.getAll("file").filter((x): x is File => x instanceof File);
  if (files.length === 0) {
    return NextResponse.json({ error: "No files uploaded under 'file' field" }, { status: 400 });
  }

  const saved: Array<{ name: string; bytes: number; path: string }> = [];
  const rejected: Array<{ name: string; reason: string }> = [];

  for (const file of files) {
    const ext = path.extname(file.name).toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      rejected.push({ name: file.name, reason: `extension '${ext}' not allowed` });
      continue;
    }
    if (file.size > MAX_BYTES) {
      rejected.push({ name: file.name, reason: `exceeds ${MAX_BYTES} byte limit` });
      continue;
    }
    const outPath = path.join(targetDir, safeFilename(file.name));
    const buf = Buffer.from(await file.arrayBuffer());
    await fs.writeFile(outPath, buf);
    saved.push({ name: file.name, bytes: buf.length, path: outPath });
  }

  return NextResponse.json({ ok: true, saved, rejected, target_dir: targetDirRaw });
}

export const config = {
  api: {
    bodyParser: false,
  },
};
