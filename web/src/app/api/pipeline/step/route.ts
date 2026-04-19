import { NextRequest } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import {
  historyPath,
  projectExists,
  projectRoot,
  ensureProjectDirs,
} from "@/lib/project-context";
import { appendJsonLine } from "@/lib/atomic-write";

interface StepRequest {
  project_slug: string;
  command: string;
  rationale?: string;
  expert_slug?: string;
  estimated_duration_seconds?: number;
}

const ALLOWED_COMMANDS = new Map<string, RegExp>([
  ["beat analyze", /^beat\s+analyze(\s|$)/],
  ["beat drops", /^beat\s+drops(\s|$)/],
  ["beat bpm", /^beat\s+bpm(\s|$)/],
  ["visual-quality", /^visual-quality(\s|$)/],
  ["verify", /^verify(\s|$)/],
  ["qa gate", /^qa\s+gate(\s|$)/],
  ["find line", /^find\s+line(\s|$)/],
  ["find shot", /^find\s+shot(\s|$)/],
  ["video info", /^video\s+info(\s|$)/],
]);

const MAX_DURATION_MS = 30_000;

function classifyCommand(command: string): { allowed: boolean; allowlistKey: string | null } {
  const trimmed = command.trim();
  for (const [key, re] of ALLOWED_COMMANDS) {
    if (re.test(trimmed)) return { allowed: true, allowlistKey: key };
  }
  return { allowed: false, allowlistKey: null };
}

function tokenize(input: string): string[] {
  const tokens: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(input)) !== null) {
    tokens.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  return tokens.filter(Boolean);
}

function sseMessage(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export async function POST(req: NextRequest) {
  let body: StepRequest;
  try {
    body = (await req.json()) as StepRequest;
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }

  if (!body.project_slug || !body.command) {
    return new Response(
      JSON.stringify({ error: "project_slug and command are required" }),
      { status: 400, headers: { "content-type": "application/json" } }
    );
  }

  if (!(await projectExists(body.project_slug))) {
    return new Response(
      JSON.stringify({ error: `Project not found: ${body.project_slug}` }),
      { status: 404, headers: { "content-type": "application/json" } }
    );
  }

  const { allowed, allowlistKey } = classifyCommand(body.command);
  if (!allowed) {
    return new Response(
      JSON.stringify({
        error: `Command not on allowlist. Allowed: ${Array.from(ALLOWED_COMMANDS.keys()).join(", ")}.`,
      }),
      { status: 403, headers: { "content-type": "application/json" } }
    );
  }

  if (
    body.estimated_duration_seconds != null &&
    body.estimated_duration_seconds > 30
  ) {
    return new Response(
      JSON.stringify({
        error:
          "Estimated duration exceeds 30s cap. Use the pipeline runner UI for longer jobs.",
      }),
      { status: 413, headers: { "content-type": "application/json" } }
    );
  }

  const tokens = tokenize(body.command);
  const ffBinary =
    process.env.FF_BINARY ?? path.join(PROJECT_ROOT, "tools", ".venv", "bin", "ff");

  await ensureProjectDirs(body.project_slug);

  const startedAt = new Date().toISOString();
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      const send = (event: string, data: unknown) => {
        controller.enqueue(encoder.encode(sseMessage(event, data)));
      };

      send("start", {
        command: body.command,
        allowlist_key: allowlistKey,
        cwd: projectRoot(body.project_slug),
      });

      const proc = spawn(ffBinary, tokens, {
        cwd: projectRoot(body.project_slug),
        env: {
          ...process.env,
          PATH: `${path.dirname(ffBinary)}:${process.env.PATH ?? ""}`,
        },
      });

      let stdout = "";
      let stderr = "";
      let killedByTimeout = false;

      const timer = setTimeout(() => {
        killedByTimeout = true;
        proc.kill("SIGKILL");
      }, MAX_DURATION_MS);

      proc.stdout.on("data", (chunk: Buffer) => {
        const text = chunk.toString("utf8");
        stdout += text;
        send("stdout", { text });
      });

      proc.stderr.on("data", (chunk: Buffer) => {
        const text = chunk.toString("utf8");
        stderr += text;
        send("stderr", { text });
      });

      proc.on("error", async (err) => {
        clearTimeout(timer);
        send("error", { message: err.message });
        controller.close();
      });

      proc.on("exit", async (code) => {
        clearTimeout(timer);
        const finishedAt = new Date().toISOString();
        const exitCode = killedByTimeout ? 124 : code ?? -1;

        try {
          await appendJsonLine(
            historyPath(body.project_slug, "qa-report"),
            {
              kind: "pipeline-run",
              ts: startedAt,
              finished_ts: finishedAt,
              expert_slug: body.expert_slug ?? null,
              rationale: body.rationale ?? null,
              command: body.command,
              allowlist_key: allowlistKey,
              exit_code: exitCode,
              killed_by_timeout: killedByTimeout,
              stdout_tail: stdout.slice(-2000),
              stderr_tail: stderr.slice(-2000),
            }
          );
        } catch {
          /* journal write best-effort */
        }

        send("done", {
          exit_code: exitCode,
          killed_by_timeout: killedByTimeout,
          stdout_bytes: stdout.length,
          stderr_bytes: stderr.length,
          finished_at: finishedAt,
        });
        controller.close();
      });
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}

export async function GET() {
  return new Response(
    JSON.stringify({
      error: "Use POST to run an allowlisted pipeline step.",
      allowlist: Array.from(ALLOWED_COMMANDS.keys()),
    }),
    { status: 405, headers: { "content-type": "application/json" } }
  );
}
