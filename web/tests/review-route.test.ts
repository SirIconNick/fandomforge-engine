import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/project/[slug]/review/route";
import { PROJECT_ROOT } from "@/lib/fs";

const TEST_SLUG = "__test_review_route__";
const projDir = path.join(PROJECT_ROOT, "projects", TEST_SLUG);
const reviewPath = path.join(projDir, "data", "post-render-review.json");

const SAMPLE_REVIEW = {
  schema_version: 1,
  project_slug: TEST_SLUG,
  video_path: `projects/${TEST_SLUG}/exports/graded.mp4`,
  generated_at: "2026-04-19T14:00:00+00:00",
  overall: "green",
  overall_verdict: "pass",
  score: 94.5,
  grade: "A",
  ship_recommendation: "Green across the board. Safe to pull into the NLE.",
  dimensions: [
    {
      name: "technical",
      verdict: "pass",
      score: 100,
      findings: [],
      measurements: { width: 1920, height: 1080, fps: 24 },
    },
  ],
};

async function cleanup() {
  try {
    await fs.rm(projDir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
}

describe("/api/project/[slug]/review GET", () => {
  beforeAll(async () => {
    await cleanup();
  });
  afterAll(async () => {
    await cleanup();
  });

  it("returns 404 when no review has been produced", async () => {
    const res = await GET(
      new NextRequest(`http://localhost/api/project/${TEST_SLUG}/review`),
      { params: Promise.resolve({ slug: TEST_SLUG }) }
    );
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error).toMatch(/not found/i);
  });

  it("returns the saved review when it exists", async () => {
    await fs.mkdir(path.dirname(reviewPath), { recursive: true });
    await fs.writeFile(reviewPath, JSON.stringify(SAMPLE_REVIEW), "utf8");

    const res = await GET(
      new NextRequest(`http://localhost/api/project/${TEST_SLUG}/review`),
      { params: Promise.resolve({ slug: TEST_SLUG }) }
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.project_slug).toBe(TEST_SLUG);
    expect(body.grade).toBe("A");
    expect(body.score).toBe(94.5);
    expect(body.dimensions).toHaveLength(1);
  });
});
