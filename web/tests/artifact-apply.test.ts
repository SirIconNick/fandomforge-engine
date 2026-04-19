import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import { POST as apply } from "@/app/api/artifacts/apply/route";
import { POST as rollback } from "@/app/api/artifacts/rollback/route";
import { GET as read } from "@/app/api/artifacts/read/route";
import { PROJECT_ROOT } from "@/lib/fs";

const TEST_SLUG = "__test_apply_route__";
const projDir = path.join(PROJECT_ROOT, "projects", TEST_SLUG);
const dataDir = path.join(projDir, "data");
const historyDir = path.join(projDir, ".history");

function applyReq(body: unknown): Request {
  return new Request("http://localhost/api/artifacts/apply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
function rollbackReq(body: unknown): Request {
  return new Request("http://localhost/api/artifacts/rollback", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
function readReq(project: string, artifact: string): Request {
  return new Request(
    `http://localhost/api/artifacts/read?project=${encodeURIComponent(project)}&artifact=${encodeURIComponent(artifact)}`
  );
}

async function cleanup() {
  try {
    await fs.rm(projDir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
}

describe("/api/artifacts/apply + rollback + read", () => {
  beforeAll(async () => {
    await cleanup();
  });

  afterAll(async () => {
    await cleanup();
  });

  beforeEach(async () => {
    await cleanup();
    await fs.mkdir(dataDir, { recursive: true });
    await fs.mkdir(historyDir, { recursive: true });
  });

  it("applies a patch, writes atomically, and journals", async () => {
    const patch = [
      {
        op: "add",
        path: "/schema_version",
        value: 1,
      },
      { op: "add", path: "/project_slug", value: TEST_SLUG },
      { op: "add", path: "/target_color_space", value: "Rec.709" },
      { op: "add", path: "/per_source", value: {} },
    ];
    const res = await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch,
      }) as never
    );
    const body = await res.json();
    expect(res.status).toBe(200);
    expect(body.ok).toBe(true);

    const onDisk = JSON.parse(
      await fs.readFile(path.join(dataDir, "color-plan.json"), "utf8")
    );
    expect(onDisk.target_color_space).toBe("Rec.709");

    const journal = await fs.readFile(
      path.join(historyDir, "color-plan.jsonl"),
      "utf8"
    );
    const entry = JSON.parse(journal.trim());
    expect(entry.after_sha256).toBeTruthy();
    expect(entry.applied_ops.length).toBe(patch.length);
  });

  it("rejects a patch that would produce an invalid schema", async () => {
    const bad = [
      { op: "add", path: "/schema_version", value: 1 },
      { op: "add", path: "/project_slug", value: TEST_SLUG },
      { op: "add", path: "/target_color_space", value: "TotallyWrong" },
      { op: "add", path: "/per_source", value: {} },
    ];
    const res = await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch: bad,
      }) as never
    );
    const body = await res.json();
    expect(res.status).toBe(422);
    expect(body.code).toBe("schema_failed");
  });

  it("respects expected_sha256 and returns 409 on mismatch", async () => {
    await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch: [
          { op: "add", path: "/schema_version", value: 1 },
          { op: "add", path: "/project_slug", value: TEST_SLUG },
          { op: "add", path: "/target_color_space", value: "Rec.709" },
          { op: "add", path: "/per_source", value: {} },
        ],
      }) as never
    );

    const stale = await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch: [{ op: "replace", path: "/target_color_space", value: "sRGB" }],
        expected_sha256: "0".repeat(64),
      }) as never
    );
    expect(stale.status).toBe(409);
    const body = await stale.json();
    expect(body.code).toBe("sha_mismatch");
  });

  it("rolls back the most recent entry", async () => {
    await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch: [
          { op: "add", path: "/schema_version", value: 1 },
          { op: "add", path: "/project_slug", value: TEST_SLUG },
          { op: "add", path: "/target_color_space", value: "Rec.709" },
          { op: "add", path: "/per_source", value: {} },
        ],
      }) as never
    );
    await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch: [{ op: "replace", path: "/target_color_space", value: "sRGB" }],
      }) as never
    );
    const res = await rollback(
      rollbackReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        steps: 1,
      }) as never
    );
    expect(res.status).toBe(200);
    const onDisk = JSON.parse(
      await fs.readFile(path.join(dataDir, "color-plan.json"), "utf8")
    );
    expect(onDisk.target_color_space).toBe("Rec.709");
  });

  it("read route returns exists:false for missing artifact", async () => {
    const res = await read(readReq(TEST_SLUG, "color-plan") as never);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.exists).toBe(false);
    expect(body.data).toBeNull();
  });

  it("read route returns sha and data for existing artifact", async () => {
    await apply(
      applyReq({
        project_slug: TEST_SLUG,
        artifact: "color-plan",
        patch: [
          { op: "add", path: "/schema_version", value: 1 },
          { op: "add", path: "/project_slug", value: TEST_SLUG },
          { op: "add", path: "/target_color_space", value: "Rec.709" },
          { op: "add", path: "/per_source", value: {} },
        ],
      }) as never
    );
    const res = await read(readReq(TEST_SLUG, "color-plan") as never);
    const body = await res.json();
    expect(body.exists).toBe(true);
    expect(body.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(body.data.target_color_space).toBe("Rec.709");
  });

  it("rejects patch on unknown project", async () => {
    const res = await apply(
      applyReq({
        project_slug: "__does_not_exist__",
        artifact: "color-plan",
        patch: [],
      }) as never
    );
    expect(res.status).toBe(404);
  });
});
