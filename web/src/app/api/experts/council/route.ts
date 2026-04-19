import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import type {
  MessageParam,
  TextBlockParam,
  ToolUnion,
} from "@anthropic-ai/sdk/resources/messages";
import { loadExperts, type ExpertAgent } from "@/lib/fs";
import {
  loadProjectArtifacts,
  projectExists,
  renderProjectContextMarkdown,
} from "@/lib/project-context";
import type { ArtifactType } from "@/lib/schemas";
import { recordChatUsage } from "@/lib/chat-usage";

interface CouncilRequest {
  expert_slugs: string[];
  project_slug?: string;
  question: string;
}

interface ExpertResponse {
  expert_slug: string;
  ok: boolean;
  reply: string;
  patches: Array<{
    tool_use_id: string;
    expert_slug: string;
    artifact: ArtifactType;
    rationale: string;
    patch: Array<Record<string, unknown>>;
  }>;
  error: string | null;
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
  } | null;
}

const ARTIFACT_ENUM: ArtifactType[] = [
  "edit-plan",
  "beat-map",
  "shot-list",
  "color-plan",
  "transition-plan",
  "audio-plan",
  "title-plan",
  "qa-report",
  "fandoms",
];

function proposeTool(): ToolUnion {
  return {
    name: "propose_artifact_patch",
    description:
      "Propose a JSON Patch (RFC 6902) to one of the project artifacts. Include rationale. The patch is advisory until the user accepts.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["artifact", "rationale", "patch"],
      properties: {
        artifact: { type: "string", enum: ARTIFACT_ENUM },
        rationale: { type: "string" },
        patch: {
          type: "array",
          items: {
            type: "object",
            required: ["op", "path"],
            properties: {
              op: {
                type: "string",
                enum: ["add", "remove", "replace", "move", "copy", "test"],
              },
              path: { type: "string" },
              value: {},
              from: { type: "string" },
            },
          },
        },
      },
    },
  };
}

async function buildSharedProjectBlock(
  projectSlug: string | undefined
): Promise<TextBlockParam | null> {
  if (!projectSlug || !(await projectExists(projectSlug))) return null;
  const artifacts = await loadProjectArtifacts(projectSlug);
  const context = renderProjectContextMarkdown(projectSlug, artifacts);
  return {
    type: "text",
    text: context,
    cache_control: { type: "ephemeral" },
  };
}

async function askExpert(
  client: Anthropic,
  expert: ExpertAgent,
  question: string,
  sharedProjectBlock: TextBlockParam | null,
  councilContext: string
): Promise<ExpertResponse> {
  const system: TextBlockParam[] = [
    {
      type: "text",
      text: `You are the FandomForge ${expert.name}. Follow the system prompt below.\n\n${expert.content}`,
      cache_control: { type: "ephemeral" },
    },
    {
      type: "text",
      text: [
        "You are being consulted as part of an expert council.",
        "Other experts are answering the same question in parallel.",
        "Give YOUR specialist perspective — do not try to cover every angle yourself.",
        "Disagreement between experts is expected and useful — state your view plainly even if others might disagree.",
        "You can propose structured edits via propose_artifact_patch. The user reviews all patches before they are applied.",
        councilContext,
      ].join("\n"),
      cache_control: { type: "ephemeral" },
    },
  ];
  if (sharedProjectBlock) system.push(sharedProjectBlock);

  const messages: MessageParam[] = [{ role: "user", content: question }];

  try {
    const res = await client.messages.create({
      model: process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-5-20250929",
      max_tokens: 2048,
      system,
      tools: [proposeTool()],
      messages,
    });

    let reply = "";
    const patches: ExpertResponse["patches"] = [];
    for (const block of res.content) {
      if (block.type === "text") {
        reply += (reply ? "\n\n" : "") + block.text;
      } else if (block.type === "tool_use" && block.name === "propose_artifact_patch") {
        const input = block.input as {
          artifact?: ArtifactType;
          rationale?: string;
          patch?: Array<Record<string, unknown>>;
        };
        if (input.artifact && Array.isArray(input.patch)) {
          patches.push({
            tool_use_id: block.id,
            expert_slug: expert.slug,
            artifact: input.artifact,
            rationale: typeof input.rationale === "string" ? input.rationale : "",
            patch: input.patch,
          });
        }
      }
    }

    return {
      expert_slug: expert.slug,
      ok: true,
      reply,
      patches,
      error: null,
      usage: {
        input_tokens: res.usage.input_tokens,
        output_tokens: res.usage.output_tokens,
        cache_creation_input_tokens: res.usage.cache_creation_input_tokens ?? 0,
        cache_read_input_tokens: res.usage.cache_read_input_tokens ?? 0,
      },
    };
  } catch (err) {
    return {
      expert_slug: expert.slug,
      ok: false,
      reply: "",
      patches: [],
      error: err instanceof Error ? err.message : String(err),
      usage: null,
    };
  }
}

export async function POST(req: NextRequest) {
  let body: CouncilRequest;
  try {
    body = (await req.json()) as CouncilRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (
    !Array.isArray(body.expert_slugs) ||
    body.expert_slugs.length < 2 ||
    body.expert_slugs.length > 4
  ) {
    return NextResponse.json(
      { error: "Pick between 2 and 4 experts for a council." },
      { status: 400 }
    );
  }
  if (!body.question || !body.question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "ANTHROPIC_API_KEY is not set. Add it to web/.env.local." },
      { status: 500 }
    );
  }

  const experts = await loadExperts();
  const chosen = body.expert_slugs
    .map((slug) => experts.find((e) => e.slug === slug))
    .filter((e): e is ExpertAgent => Boolean(e));

  if (chosen.length !== body.expert_slugs.length) {
    return NextResponse.json(
      { error: "One or more expert slugs did not resolve to a known expert." },
      { status: 404 }
    );
  }

  const client = new Anthropic({ apiKey });
  const sharedBlock = await buildSharedProjectBlock(body.project_slug);
  const councilContext = `Council members: ${chosen.map((e) => e.slug).join(", ")}.`;

  const responses = await Promise.all(
    chosen.map((expert) =>
      askExpert(client, expert, body.question, sharedBlock, councilContext)
    )
  );

  await Promise.all(
    responses.map((r) =>
      r.usage
        ? recordChatUsage({
            ts: new Date().toISOString(),
            expert_slug: r.expert_slug,
            project_slug: body.project_slug ?? null,
            input_tokens: r.usage.input_tokens,
            output_tokens: r.usage.output_tokens,
            cache_creation_input_tokens: r.usage.cache_creation_input_tokens,
            cache_read_input_tokens: r.usage.cache_read_input_tokens,
            stop_reason: null,
            patches_proposed: r.patches.length,
            pipeline_runs_proposed: 0,
            mode: "council",
          })
        : Promise.resolve()
    )
  );

  const allPatches = responses.flatMap((r) => r.patches);
  const conflicts: Array<{
    artifact: string;
    path: string;
    proposers: string[];
  }> = [];
  const seen = new Map<string, string[]>();
  for (const p of allPatches) {
    for (const op of p.patch) {
      const key = `${p.artifact}::${op.path}`;
      if (!seen.has(key)) seen.set(key, []);
      seen.get(key)!.push(p.expert_slug);
    }
  }
  for (const [key, proposers] of seen) {
    if (proposers.length > 1) {
      const [artifact, ...rest] = key.split("::");
      conflicts.push({
        artifact: artifact ?? "",
        path: rest.join("::"),
        proposers: [...new Set(proposers)],
      });
    }
  }

  return NextResponse.json({
    ok: true,
    responses,
    conflicts,
  });
}
