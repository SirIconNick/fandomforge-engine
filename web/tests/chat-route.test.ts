import { describe, it, expect, beforeEach, vi } from "vitest";

const createMock = vi.fn();
class MockCtor {
  messages = { create: createMock };
  constructor(_opts: unknown) {}
}
const ctorSpy = vi.fn((opts: unknown) => new MockCtor(opts));

vi.mock("@anthropic-ai/sdk", () => ({
  default: function AnthropicMock(this: MockCtor, opts: unknown) {
    ctorSpy(opts);
    this.messages = { create: createMock };
  } as unknown as typeof MockCtor,
}));

vi.mock("@/lib/fs", async () => {
  const actual = await vi.importActual<typeof import("@/lib/fs")>("@/lib/fs");
  return {
    ...actual,
    loadExperts: vi.fn(async () => [
      {
        slug: "beat-mapper",
        name: "beat-mapper",
        description: "test",
        color: "red",
        model: "sonnet",
        content: "beat-mapper system prompt",
      },
      {
        slug: "title-designer",
        name: "title-designer",
        description: "test",
        color: "pink",
        model: "sonnet",
        content: "title-designer system prompt",
      },
    ]),
  };
});

vi.mock("@/lib/project-context", () => ({
  loadProjectArtifacts: vi.fn(async () => ({})),
  projectExists: vi.fn(async () => false),
  renderProjectContextMarkdown: vi.fn(() => "# empty"),
}));

const { POST } = await import("@/app/api/experts/chat/route");

function chatReq(body: unknown): Request {
  return new Request("http://localhost/api/experts/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("/api/experts/chat", () => {
  beforeEach(() => {
    createMock.mockReset();
    ctorSpy.mockClear();
    process.env.ANTHROPIC_API_KEY = "sk-test";
  });

  it("returns plain text reply when model emits only a text block", async () => {
    createMock.mockResolvedValue({
      content: [{ type: "text", text: "hello there" }],
      stop_reason: "end_turn",
      usage: { input_tokens: 10, output_tokens: 5 },
    });
    const res = await POST(
      chatReq({
        expert_slug: "title-designer",
        messages: [{ role: "user", content: "hi" }],
      }) as never
    );
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.reply).toBe("hello there");
    expect(body.patches).toEqual([]);
    expect(body.pipeline_runs).toEqual([]);
  });

  it("extracts artifact-patch tool_use blocks into patches", async () => {
    createMock.mockResolvedValue({
      content: [
        { type: "text", text: "proposing a small fix" },
        {
          type: "tool_use",
          id: "toolu_1",
          name: "propose_artifact_patch",
          input: {
            artifact: "color-plan",
            rationale: "warmer grade",
            patch: [{ op: "replace", path: "/global_lut_intensity", value: 0.7 }],
          },
        },
      ],
      stop_reason: "tool_use",
      usage: { input_tokens: 200, output_tokens: 80 },
    });
    const res = await POST(
      chatReq({
        expert_slug: "title-designer",
        messages: [{ role: "user", content: "warmer grade?" }],
      }) as never
    );
    const body = await res.json();
    expect(body.patches.length).toBe(1);
    expect(body.patches[0].artifact).toBe("color-plan");
    expect(body.patches[0].expert_slug).toBe("title-designer");
    expect(body.patches[0].patch).toHaveLength(1);
  });

  it("exposes the pipeline tool only to pipeline-enabled experts", async () => {
    createMock.mockResolvedValue({
      content: [{ type: "text", text: "ok" }],
      stop_reason: "end_turn",
      usage: { input_tokens: 5, output_tokens: 1 },
    });

    await POST(
      chatReq({
        expert_slug: "title-designer",
        messages: [{ role: "user", content: "x" }],
      }) as never
    );
    await POST(
      chatReq({
        expert_slug: "beat-mapper",
        messages: [{ role: "user", content: "x" }],
      }) as never
    );

    const firstCallTools = (createMock.mock.calls[0]?.[0] as { tools: { name: string }[] }).tools;
    const secondCallTools = (createMock.mock.calls[1]?.[0] as { tools: { name: string }[] }).tools;
    expect(firstCallTools.map((t) => t.name)).toEqual(["propose_artifact_patch"]);
    expect(secondCallTools.map((t) => t.name)).toEqual([
      "propose_artifact_patch",
      "run_pipeline_step",
    ]);
  });

  it("returns 404 for unknown expert", async () => {
    const res = await POST(
      chatReq({
        expert_slug: "not-a-real-expert",
        messages: [{ role: "user", content: "x" }],
      }) as never
    );
    expect(res.status).toBe(404);
  });

  it("returns 500 when ANTHROPIC_API_KEY is missing", async () => {
    delete process.env.ANTHROPIC_API_KEY;
    const res = await POST(
      chatReq({
        expert_slug: "title-designer",
        messages: [{ role: "user", content: "x" }],
      }) as never
    );
    expect(res.status).toBe(500);
    process.env.ANTHROPIC_API_KEY = "sk-test";
  });
});
