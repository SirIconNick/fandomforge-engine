import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import { NextRequest } from "next/server";
import { PROJECT_ROOT } from "@/lib/fs";

// These tests mock runFF so they never spawn a real child process. They
// verify the route wrappers: argument shape, missing-project handling, and
// response envelopes around runFF's exit.

vi.mock("@/lib/ff-api", () => ({
  runFF: vi.fn(),
  projectDir: (slug: string) => path.join(PROJECT_ROOT, "projects", slug),
  projectDataPath: (slug: string, artifact: string) =>
    path.join(PROJECT_ROOT, "projects", slug, "data", artifact),
}));

const TEST_SLUG = "__test_actions__";
const projDir = path.join(PROJECT_ROOT, "projects", TEST_SLUG);
const exportsDir = path.join(projDir, "exports");

async function setupProject() {
  await fs.mkdir(exportsDir, { recursive: true });
}

async function cleanup() {
  await fs.rm(projDir, { recursive: true, force: true });
}

describe("POST /api/project/[slug]/render", () => {
  beforeEach(async () => {
    await cleanup();
  });
  afterEach(async () => {
    await cleanup();
    vi.clearAllMocks();
  });

  it("returns 404 when project doesn't exist", async () => {
    const { POST } = await import("@/app/api/project/[slug]/render/route");
    const res = await POST(
      new NextRequest(`http://localhost/api/project/${TEST_SLUG}/render`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ slug: TEST_SLUG }) }
    );
    expect(res.status).toBe(404);
  });

  it("reports the output path and size after a successful render", async () => {
    await setupProject();
    const { runFF } = await import("@/lib/ff-api");
    (runFF as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      exitCode: 0,
      stdout: "rendered",
      stderr: "",
    });
    await fs.writeFile(path.join(exportsDir, "roughcut.mp4"), Buffer.alloc(1024));

    const { POST } = await import("@/app/api/project/[slug]/render/route");
    const res = await POST(
      new NextRequest(`http://localhost/api/project/${TEST_SLUG}/render`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ slug: TEST_SLUG }) }
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.bytes).toBe(1024);
    expect(body.output_path).toContain("roughcut.mp4");
  });
});

describe("POST /api/project/[slug]/color", () => {
  beforeEach(async () => {
    await cleanup();
  });
  afterEach(async () => {
    await cleanup();
    vi.clearAllMocks();
  });

  it("passes the expected args to runFF", async () => {
    await setupProject();
    const { runFF } = await import("@/lib/ff-api");
    (runFF as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      exitCode: 0,
      stdout: "graded",
      stderr: "",
    });
    await fs.writeFile(path.join(exportsDir, "graded.mp4"), Buffer.alloc(512));

    const { POST } = await import("@/app/api/project/[slug]/color/route");
    await POST(
      new NextRequest(`http://localhost/api/project/${TEST_SLUG}/color`, {
        method: "POST",
        body: JSON.stringify({ input: "roughcut.mp4", preset: "tactical" }),
      }),
      { params: Promise.resolve({ slug: TEST_SLUG }) }
    );
    const args = (runFF as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(args).toContain("color");
    expect(args).toContain("--project");
    expect(args).toContain(TEST_SLUG);
    expect(args).toContain("--preset");
    expect(args).toContain("tactical");
  });
});

describe("POST /api/project/[slug]/export-nle", () => {
  beforeEach(async () => {
    await cleanup();
  });
  afterEach(async () => {
    await cleanup();
    vi.clearAllMocks();
  });

  it("returns an outputs array with both fcpxml and edl paths", async () => {
    await setupProject();
    const { runFF } = await import("@/lib/ff-api");
    (runFF as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      exitCode: 0,
      stdout: "exported",
      stderr: "",
    });
    await fs.writeFile(path.join(exportsDir, "timeline.fcpxml"), "<fcpxml/>");
    await fs.writeFile(path.join(exportsDir, "timeline.edl"), "EDL");

    const { POST } = await import("@/app/api/project/[slug]/export-nle/route");
    const res = await POST(
      new NextRequest(`http://localhost/api/project/${TEST_SLUG}/export-nle`, {
        method: "POST",
        body: JSON.stringify({ format: "both" }),
      }),
      { params: Promise.resolve({ slug: TEST_SLUG }) }
    );
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.outputs).toHaveLength(2);
    expect(body.outputs.some((p: string) => p.endsWith(".fcpxml"))).toBe(true);
    expect(body.outputs.some((p: string) => p.endsWith(".edl"))).toBe(true);
  });
});
