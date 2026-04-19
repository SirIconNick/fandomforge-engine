import { describe, it, expect } from "vitest";
import { validateArtifact, listRegisteredArtifacts } from "@/lib/schemas";

describe("schemas (Ajv against real JSON Schemas)", () => {
  it("registers all expected artifact types from disk", async () => {
    const types = await listRegisteredArtifacts();
    expect(types).toContain("edit-plan");
    expect(types).toContain("color-plan");
    expect(types).toContain("transition-plan");
    expect(types).toContain("audio-plan");
    expect(types).toContain("title-plan");
    expect(types).toContain("beat-map");
    expect(types).toContain("shot-list");
    expect(types).toContain("qa-report");
  });

  it("accepts a minimal valid color-plan", async () => {
    const ok = await validateArtifact("color-plan", {
      schema_version: 1,
      project_slug: "demo",
      target_color_space: "Rec.709",
      per_source: {},
    });
    expect(ok.ok).toBe(true);
    expect(ok.errors).toBeNull();
  });

  it("rejects a color-plan missing required fields", async () => {
    const bad = await validateArtifact("color-plan", {
      schema_version: 1,
      project_slug: "demo",
    });
    expect(bad.ok).toBe(false);
    expect(bad.errors?.length).toBeGreaterThan(0);
  });

  it("rejects an unknown enum value on target_color_space", async () => {
    const bad = await validateArtifact("color-plan", {
      schema_version: 1,
      project_slug: "demo",
      target_color_space: "MadeUp",
      per_source: {},
    });
    expect(bad.ok).toBe(false);
    const paths = (bad.errors ?? []).map((e) => e.instancePath);
    expect(paths.join(" ")).toContain("/target_color_space");
  });

  it("accepts an empty transition-plan with the required scaffolding", async () => {
    const ok = await validateArtifact("transition-plan", {
      schema_version: 1,
      project_slug: "demo",
      fps: 24,
      transitions: [],
    });
    expect(ok.ok).toBe(true);
  });

  it("accepts a fandoms.json with user-provided entries", async () => {
    const ok = await validateArtifact("fandoms", {
      schema_version: 1,
      fandoms: [
        {
          name: "Arcane",
          medium: "anime",
          iconic_scenes: [
            {
              scene: "Jinx on bridge",
              timestamp: "12:45",
              emotion: "grief",
              sync_targets: ["breakdown"],
            },
          ],
        },
      ],
    });
    expect(ok.ok).toBe(true);
  });

  it("rejects fandoms.json with bad timestamp format", async () => {
    const bad = await validateArtifact("fandoms", {
      schema_version: 1,
      fandoms: [
        {
          name: "Arcane",
          iconic_scenes: [
            { scene: "Jinx on bridge", timestamp: "not-a-timestamp" },
          ],
        },
      ],
    });
    expect(bad.ok).toBe(false);
  });

  it("returns a missing-schema error for an unknown artifact type", async () => {
    const bad = await validateArtifact(
      // deliberately wrong — force the guard
      "nope" as unknown as Parameters<typeof validateArtifact>[0],
      {}
    );
    expect(bad.ok).toBe(false);
    expect(bad.errors?.[0]?.keyword).toBe("missing-schema");
  });
});
