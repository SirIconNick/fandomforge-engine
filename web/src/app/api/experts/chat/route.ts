import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import type { MessageParam, TextBlockParam, ToolUnion } from "@anthropic-ai/sdk/resources/messages";
import { loadExperts, type ExpertAgent } from "@/lib/fs";
import {
  loadProjectArtifacts,
  projectExists,
  renderProjectContextMarkdown,
} from "@/lib/project-context";
import type { ArtifactType } from "@/lib/schemas";
import { recordChatUsage } from "@/lib/chat-usage";

interface ChatRequest {
  expert_slug: string;
  project_slug?: string;
  messages: Array<{ role: "user" | "assistant"; content: string }>;
}

export interface ProposedPatch {
  tool_use_id: string;
  expert_slug: string;
  artifact: ArtifactType;
  rationale: string;
  patch: Array<Record<string, unknown>>;
}

export interface ProposedPipelineRun {
  tool_use_id: string;
  expert_slug: string;
  command: string;
  rationale: string;
  estimated_duration_seconds?: number;
}

export interface ChatResponseBody {
  ok: true;
  reply: string;
  patches: ProposedPatch[];
  pipeline_runs: ProposedPipelineRun[];
  stop_reason: string | null;
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
  };
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

const PIPELINE_ENABLED = new Set(["beat-mapper", "pipeline-tuner", "qa-reviewer", "shot-curator"]);

function proposeArtifactPatchTool(): ToolUnion {
  return {
    name: "propose_artifact_patch",
    description:
      "Propose a JSON Patch (RFC 6902) to one of the project artifacts. Always emit minimal surgical patches. Never replace the whole document unless the user explicitly asked. The patch is advisory and will be reviewed by the user before it is applied.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["artifact", "rationale", "patch"],
      properties: {
        artifact: {
          type: "string",
          enum: ARTIFACT_ENUM,
          description: "Which artifact this patch targets.",
        },
        rationale: {
          type: "string",
          description: "One or two sentences explaining why this change improves the artifact. Shown to the user in the review UI.",
        },
        patch: {
          type: "array",
          description: "An RFC 6902 JSON Patch operation list. Each item is an object with op, path, and value/from as appropriate.",
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

function runPipelineStepTool(): ToolUnion {
  return {
    name: "run_pipeline_step",
    description:
      "Propose running a FandomForge CLI command (ff <subcommand>) against the current project. The user reviews and confirms the run. Only short operations (< 30 seconds) should be proposed here. Redirect long-running work to the pipeline runner UI.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["command", "rationale"],
      properties: {
        command: {
          type: "string",
          description: "The ff subcommand and arguments as a single string, e.g. 'beat analyze projects/my-edit/assets/song.mp3'. No leading 'ff'.",
        },
        rationale: {
          type: "string",
          description: "Why this run helps. Shown in the confirmation card.",
        },
        estimated_duration_seconds: {
          type: "number",
          minimum: 1,
          maximum: 30,
          description: "Best guess of how long the run will take. The UI redirects commands estimated over 30s to the pipeline runner page.",
        },
      },
    },
  };
}

function buildTools(expertSlug: string): ToolUnion[] {
  const tools: ToolUnion[] = [proposeArtifactPatchTool()];
  if (PIPELINE_ENABLED.has(expertSlug)) {
    tools.push(runPipelineStepTool());
  }
  return tools;
}

async function buildSystemBlocks(
  expert: ExpertAgent,
  projectSlug: string | undefined
): Promise<TextBlockParam[]> {
  const blocks: TextBlockParam[] = [];

  blocks.push({
    type: "text",
    text: `You are the FandomForge ${expert.name}. Follow the system prompt below.\n\n${expert.content}`,
    cache_control: { type: "ephemeral" },
  });

  blocks.push({
    type: "text",
    text: [
      "You can propose structured edits to the user's project artifacts using the propose_artifact_patch tool.",
      "Rules:",
      "1. Emit the smallest possible patch. Never replace a whole document unless explicitly asked.",
      "2. Include a short rationale so the user understands the change before approving.",
      "3. If you are not confident the patch is correct, describe it in plain text instead and ask the user.",
      "4. The user always reviews and approves patches before they are written. Do not pretend the patch was applied.",
      PIPELINE_ENABLED.has(expert.slug)
        ? "5. You may also propose quick CLI runs with run_pipeline_step for operations under 30 seconds."
        : "",
    ]
      .filter(Boolean)
      .join("\n"),
    cache_control: { type: "ephemeral" },
  });

  if (projectSlug && (await projectExists(projectSlug))) {
    const artifacts = await loadProjectArtifacts(projectSlug);
    const context = renderProjectContextMarkdown(projectSlug, artifacts);
    blocks.push({
      type: "text",
      text: context,
      cache_control: { type: "ephemeral" },
    });
  }

  return blocks;
}

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
    return NextResponse.json(
      { error: `Unknown expert '${body.expert_slug}'` },
      { status: 404 }
    );
  }

  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return NextResponse.json(
      { error: "messages array must be non-empty" },
      { status: 400 }
    );
  }

  const client = new Anthropic({ apiKey });
  const system = await buildSystemBlocks(expert, body.project_slug);
  const tools = buildTools(expert.slug);
  const messages: MessageParam[] = body.messages.map((m) => ({
    role: m.role,
    content: m.content,
  }));

  try {
    const response = await client.messages.create({
      model: process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-5-20250929",
      max_tokens: 4096,
      system,
      tools,
      messages,
    });

    let replyText = "";
    const patches: ProposedPatch[] = [];
    const pipelineRuns: ProposedPipelineRun[] = [];
    for (const block of response.content) {
      if (block.type === "text") {
        replyText += (replyText ? "\n\n" : "") + block.text;
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
      } else if (block.type === "tool_use" && block.name === "run_pipeline_step") {
        const input = block.input as {
          command?: string;
          rationale?: string;
          estimated_duration_seconds?: number;
        };
        if (input.command) {
          pipelineRuns.push({
            tool_use_id: block.id,
            expert_slug: expert.slug,
            command: input.command,
            rationale: typeof input.rationale === "string" ? input.rationale : "",
            estimated_duration_seconds: input.estimated_duration_seconds,
          });
        }
      }
    }

    await recordChatUsage({
      ts: new Date().toISOString(),
      expert_slug: expert.slug,
      project_slug: body.project_slug ?? null,
      input_tokens: response.usage.input_tokens,
      output_tokens: response.usage.output_tokens,
      cache_creation_input_tokens: response.usage.cache_creation_input_tokens ?? 0,
      cache_read_input_tokens: response.usage.cache_read_input_tokens ?? 0,
      stop_reason: response.stop_reason,
      patches_proposed: patches.length,
      pipeline_runs_proposed: pipelineRuns.length,
      mode: "chat",
    });

    const payload: ChatResponseBody = {
      ok: true,
      reply: replyText,
      patches,
      pipeline_runs: pipelineRuns,
      stop_reason: response.stop_reason,
      usage: {
        input_tokens: response.usage.input_tokens,
        output_tokens: response.usage.output_tokens,
        cache_creation_input_tokens: response.usage.cache_creation_input_tokens ?? 0,
        cache_read_input_tokens: response.usage.cache_read_input_tokens ?? 0,
      },
    };
    return NextResponse.json(payload);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: `anthropic SDK error: ${message}` },
      { status: 502 }
    );
  }
}
