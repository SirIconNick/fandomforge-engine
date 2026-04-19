import { NextRequest, NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import { projectExists } from "@/lib/project-context";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
} as const;

export function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

type Mode = "both" | "video" | "audio";
type Browser =
  | "chrome" | "chromium" | "brave" | "edge"
  | "firefox" | "safari" | "opera" | "vivaldi" | "whale";

const SUPPORTED_BROWSERS: readonly Browser[] = [
  "chrome", "chromium", "brave", "edge",
  "firefox", "safari", "opera", "vivaldi", "whale",
] as const;

interface GrabRequest {
  project_slug: string;
  url: string;
  mode?: Mode;
  resolution?: string;
  filename?: string;
  audio_format?: string;
  note?: string;
  cookies_from_browser?: Browser;
}

export async function POST(req: NextRequest) {
  let body: GrabRequest;
  try {
    body = (await req.json()) as GrabRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400, headers: CORS_HEADERS });
  }

  if (!body.project_slug || !body.url) {
    return NextResponse.json(
      { error: "project_slug and url are required" },
      { status: 400, headers: CORS_HEADERS }
    );
  }
  const mode: Mode = body.mode ?? "both";
  if (mode !== "both" && mode !== "video" && mode !== "audio") {
    return NextResponse.json(
      { error: "mode must be 'both', 'video', or 'audio'" },
      { status: 400, headers: CORS_HEADERS }
    );
  }
  if (!(await projectExists(body.project_slug))) {
    return NextResponse.json({ error: "project not found" }, { status: 404, headers: CORS_HEADERS });
  }

  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");

  const args = ["grab", "video", "--project", body.project_slug, "--url", body.url];
  if (mode === "audio") args.push("--audio-only");
  if (mode === "video") args.push("--no-audio");
  if (body.audio_format && mode === "audio") {
    args.push("--audio-format", body.audio_format);
  }
  if (mode !== "audio" && body.resolution) {
    args.push("--resolution", body.resolution);
  }
  if (body.filename) args.push("--filename", body.filename);
  if (body.note) args.push("--note", body.note);
  if (body.cookies_from_browser) {
    if (!SUPPORTED_BROWSERS.includes(body.cookies_from_browser)) {
      return NextResponse.json(
        { error: `cookies_from_browser must be one of: ${SUPPORTED_BROWSERS.join(", ")}` },
        { status: 400, headers: CORS_HEADERS }
      );
    }
    args.push("--cookies-from-browser", body.cookies_from_browser);
  }
  if (mode !== "both") args.push("--no-ingest");

  const proc = spawn(ffBinary, args, {
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      PATH: `${path.dirname(ffBinary)}:${process.env.PATH ?? ""}`,
    },
  });

  let stdout = "";
  let stderr = "";
  proc.stdout.on("data", (c: Buffer) => { stdout += c.toString("utf8"); });
  proc.stderr.on("data", (c: Buffer) => { stderr += c.toString("utf8"); });

  const exitCode: number = await new Promise((resolve) => {
    proc.on("exit", (code) => resolve(code ?? -1));
    proc.on("error", () => resolve(-1));
  });

  if (exitCode !== 0) {
    return NextResponse.json(
      {
        error: "grab failed",
        exit_code: exitCode,
        stdout: stdout.slice(-1200),
        stderr: stderr.slice(-800),
      },
      { status: 500, headers: CORS_HEADERS }
    );
  }

  return NextResponse.json(
    {
      ok: true,
      exit_code: exitCode,
      mode,
      log: (stdout + stderr).slice(-1600),
    },
    { headers: CORS_HEADERS }
  );
}
