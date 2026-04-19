import { NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

export const dynamic = "force-dynamic";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
} as const;

export function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

interface LibrarySummary {
  index_path: string;
  exists: boolean;
  roots: Array<{
    name: string;
    path: string;
    auto_fandom_rule: string;
    added_at: string;
    file_count: number;
  }>;
  fandoms: Array<{ fandom: string | null; count: number }>;
  totals: {
    sources: number;
    pending: number;
    in_progress: number;
    done: number;
    failed: number;
  };
}

export async function GET() {
  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");

  const proc = spawn(ffBinary, ["library", "summary", "--json"], {
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      PATH: `${path.dirname(ffBinary)}:${process.env.PATH ?? ""}`,
    },
  });

  let stdout = "";
  let stderr = "";
  proc.stdout.on("data", (c: Buffer) => {
    stdout += c.toString("utf8");
  });
  proc.stderr.on("data", (c: Buffer) => {
    stderr += c.toString("utf8");
  });

  const exitCode: number = await new Promise((resolve) => {
    proc.on("exit", (code) => resolve(code ?? -1));
    proc.on("error", () => resolve(-1));
  });

  if (exitCode !== 0) {
    return NextResponse.json(
      {
        error: "ff library summary failed",
        exit_code: exitCode,
        stderr: stderr.slice(-800),
      },
      { status: 500, headers: CORS_HEADERS }
    );
  }

  try {
    const summary = JSON.parse(stdout) as LibrarySummary;
    return NextResponse.json(summary, { headers: CORS_HEADERS });
  } catch (error) {
    return NextResponse.json(
      {
        error: "could not parse library summary output",
        detail: (error as Error).message,
        stdout: stdout.slice(-400),
      },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}
