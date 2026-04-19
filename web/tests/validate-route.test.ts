import { describe, it, expect } from "vitest";
import { POST } from "@/app/api/artifacts/validate/route";

function makeReq(body: unknown): Request {
  return new Request("http://localhost/api/artifacts/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("/api/artifacts/validate", () => {
  it("accepts valid input", async () => {
    const res = await POST(
      makeReq({
        artifact: "color-plan",
        data: {
          schema_version: 1,
          project_slug: "demo",
          target_color_space: "Rec.709",
          per_source: {},
        },
      }) as never
    );
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.errors).toBeNull();
  });

  it("rejects malformed input with schema errors", async () => {
    const res = await POST(
      makeReq({
        artifact: "color-plan",
        data: { schema_version: 99 },
      }) as never
    );
    const body = await res.json();
    expect(body.ok).toBe(false);
    expect(Array.isArray(body.errors)).toBe(true);
    expect(body.errors.length).toBeGreaterThan(0);
  });

  it("400s when artifact is missing", async () => {
    const res = await POST(makeReq({ data: {} }) as never);
    expect(res.status).toBe(400);
  });
});
