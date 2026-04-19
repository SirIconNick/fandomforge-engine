import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT, loadExperts } from "@/lib/fs";

interface ChatRequest {
  expert_slug: string;
  project_slug?: string;
  messages: Array<{ role: "user" | "assistant"; content: string }>;
}

/**
 * Non-streaming chat with a given expert. Loads the expert's markdown as the
 * system prompt, optionally injects the project's current artifacts as
 * grounding context, then hits the Anthropic API.
 *
 * Requires ANTHROPIC_API_KEY in the environment. Returns a clear error if
 * missing so the user knows to set it.
 */
export async function POST(req: NextRequest) {
  let body: ChatRequest;
  try {
    body = (await req.json()) as ChatRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      {
        error:
          "ANTHROPIC_API_KEY is not set. Add it to web/.env.local to enable expert chat.",
      },
      { status: 500 }
    );
  }

  const experts = await loadExperts();
  const expert = experts.find((e) => e.slug === body.expert_slug);
  if (!expert) {
    return NextResponse.json({ error: `Unknown expert '${body.expert_slug}'` }, { status: 404 });
  }

  const systemParts: string[] = [expert.content];
  if (body.project_slug) {
    const ctx = await loadProjectContext(body.project_slug);
    if (ctx) systemParts.push(ctx);
  }

  const payload = {
    model: process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-6",
    max_tokens: 2048,
    system: systemParts.join("\n\n---\n\n"),
    messages: body.messages,
  };

  const upstream = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify(payload),
  });

  if (!upstream.ok) {
    const err = await upstream.text();
    return NextResponse.json(
      { error: `anthropic API ${upstream.status}: ${err}` },
      { status: 502 }
    );
  }

  const data = (await upstream.json()) as {
    content?: Array<{ type: string; text?: string }>;
  };
  const text = (data.content ?? [])
    .filter((b) => b.type === "text")
    .map((b) => b.text ?? "")
    .join("\n");
  return NextResponse.json({ ok: true, reply: text, raw: data });
}

async function loadProjectContext(slug: string): Promise<string | null> {
  const projDir = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projDir);
  } catch {
    return null;
  }
  const dataDir = path.join(projDir, "data");
  const files = ["edit-plan.json", "beat-map.json", "shot-list.json", "qa-report.json"];
  const parts: string[] = [`# Current project: ${slug}`];
  for (const f of files) {
    const p = path.join(dataDir, f);
    try {
      const text = await fs.readFile(p, "utf8");
      parts.push(`## ${f}\n\`\`\`json\n${text}\n\`\`\``);
    } catch {
      // artifact missing — skip
    }
  }
  return parts.join("\n\n");
}
