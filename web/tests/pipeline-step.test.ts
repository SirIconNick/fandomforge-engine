import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import { GET, POST } from "@/app/api/pipeline/step/route";
import { PROJECT_ROOT } from "@/lib/fs";

const TEST_SLUG = "__test_pipeline_step__";
const projDir = path.join(PROJECT_ROOT, "projects", TEST_SLUG);

function stepReq(body: unknown): Request {
  return new Request("http://localhost/api/pipeline/step", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("/api/pipeline/step guards", () => {
  beforeAll(async () => {
    await fs.mkdir(path.join(projDir, "data"), { recursive: true });
  });
  afterAll(async () => {
    await fs.rm(projDir, { recursive: true, force: true });
  });

  it("GET returns 405 with the allowlist", async () => {
    const res = await GET();
    const body = await res.json();
    expect(res.status).toBe(405);
    expect(body.allowlist).toContain("beat analyze");
    expect(body.allowlist).toContain("qa gate");
  });

  it("rejects commands not on the allowlist", async () => {
    const res = await POST(
      stepReq({
        project_slug: TEST_SLUG,
        command: "rm -rf /",
      }) as never
    );
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.error).toMatch(/allowlist/);
  });

  it("rejects when project_slug is missing", async () => {
    const res = await POST(stepReq({ command: "beat analyze foo.mp3" }) as never);
    expect(res.status).toBe(400);
  });

  it("rejects estimated duration over 30s", async () => {
    const res = await POST(
      stepReq({
        project_slug: TEST_SLUG,
        command: "beat analyze song.mp3",
        estimated_duration_seconds: 120,
      }) as never
    );
    expect(res.status).toBe(413);
  });

  it("returns 404 for unknown project on allowlisted command", async () => {
    const res = await POST(
      stepReq({
        project_slug: "__does_not_exist_at_all__",
        command: "beat analyze song.mp3",
      }) as never
    );
    expect(res.status).toBe(404);
  });
});
