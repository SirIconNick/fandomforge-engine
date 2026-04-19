import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

type Params = Promise<{ slug: string }>;

const DEFAULT_INTERVAL_SEC = 5;

function safeJoin(base: string, relative: string): string | null {
  const resolved = path.resolve(base, relative);
  if (!resolved.startsWith(base + path.sep) && resolved !== base) return null;
  return resolved;
}

function cachedFrameCandidates(
  cacheDir: string,
  sourceId: string,
  sec: number,
  interval: number
): string[] {
  const idx = Math.max(0, Math.round(sec / interval));
  const perSourceDir = path.join(cacheDir, `${sourceId}_frames`);
  const flatDir = cacheDir;
  const padded = String(idx + 1).padStart(6, "0");
  const paddedAlt = String(idx).padStart(6, "0");
  return [
    path.join(perSourceDir, `frame_${padded}.jpg`),
    path.join(perSourceDir, `frame_${paddedAlt}.jpg`),
    path.join(flatDir, `${sourceId}_frame_${padded}.jpg`),
    path.join(flatDir, `${sourceId}_frame_${paddedAlt}.jpg`),
  ];
}

function nearestJpegInDir(dir: string): string | null {
  try {
    const entries = fs
      .readdirSync(dir)
      .filter((e) => e.endsWith(".jpg"))
      .sort();
    if (entries.length === 0) return null;
    const middle = entries[Math.floor(entries.length / 2)];
    return middle ? path.join(dir, middle) : null;
  } catch {
    return null;
  }
}

export async function GET(req: Request, { params }: { params: Params }) {
  const { slug } = await params;
  const url = new URL(req.url);
  const source = url.searchParams.get("source") ?? "";
  const timeParam = url.searchParams.get("time");
  const intervalParam = url.searchParams.get("interval");

  if (!source) {
    return NextResponse.json({ error: "missing source param" }, { status: 400 });
  }

  const projectDir = path.resolve(PROJECT_ROOT, "projects", slug);
  const cacheDir = safeJoin(projectDir, ".clip-cache");
  if (!cacheDir || !fs.existsSync(cacheDir)) {
    return NextResponse.json({ error: "clip-cache not found" }, { status: 404 });
  }

  const sec = timeParam ? Math.max(0, parseFloat(timeParam)) : 0;
  const interval = intervalParam
    ? Math.max(0.5, parseFloat(intervalParam))
    : DEFAULT_INTERVAL_SEC;

  const candidates = cachedFrameCandidates(cacheDir, source, sec, interval);
  let hit: string | null = null;
  for (const candidate of candidates) {
    const safe = safeJoin(projectDir, path.relative(projectDir, candidate));
    if (safe && fs.existsSync(safe)) {
      hit = safe;
      break;
    }
  }

  if (!hit) {
    const perSourceDir = path.join(cacheDir, `${source}_frames`);
    if (fs.existsSync(perSourceDir)) {
      hit = nearestJpegInDir(perSourceDir);
    }
  }

  if (!hit) {
    return NextResponse.json({ error: "frame not cached" }, { status: 404 });
  }

  const data = fs.readFileSync(hit);
  return new Response(new Uint8Array(data), {
    headers: {
      "Content-Type": "image/jpeg",
      "Cache-Control": "public, max-age=3600",
      "Content-Length": String(data.length),
    },
  });
}
