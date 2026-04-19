import { describe, it, expect } from "vitest";
import { summarize, type ChatUsageEntry } from "@/lib/chat-usage";

function mk(overrides: Partial<ChatUsageEntry>): ChatUsageEntry {
  return {
    ts: "2026-04-19T00:00:00Z",
    expert_slug: "beat-mapper",
    project_slug: "demo",
    input_tokens: 100,
    output_tokens: 50,
    cache_creation_input_tokens: 0,
    cache_read_input_tokens: 0,
    stop_reason: "end_turn",
    patches_proposed: 0,
    pipeline_runs_proposed: 0,
    mode: "chat",
    ...overrides,
  };
}

describe("chat-usage summarize", () => {
  it("returns zeros for an empty log", () => {
    const s = summarize([]);
    expect(s.total_turns).toBe(0);
    expect(s.cache_hit_rate).toBe(0);
    expect(s.first_ts).toBeNull();
  });

  it("computes cache hit rate from total cache reads vs total input", () => {
    const s = summarize([
      mk({ input_tokens: 100, cache_read_input_tokens: 0 }),
      mk({ input_tokens: 10, cache_read_input_tokens: 90 }),
      mk({ input_tokens: 10, cache_read_input_tokens: 190 }),
    ]);
    // input total = 120 direct + 280 cache = 400; cache_read = 280; rate = 280/400 = 0.7
    expect(s.cache_hit_rate).toBeCloseTo(0.7, 3);
  });

  it("groups by expert and tracks patches + modes", () => {
    const entries = [
      mk({
        expert_slug: "color-grader",
        patches_proposed: 2,
        mode: "chat",
      }),
      mk({
        expert_slug: "color-grader",
        patches_proposed: 1,
        mode: "chat",
      }),
      mk({ expert_slug: "story-weaver", mode: "council" }),
    ];
    const s = summarize(entries);
    expect(s.by_expert["color-grader"]?.turns).toBe(2);
    expect(s.by_expert["story-weaver"]?.turns).toBe(1);
    expect(s.by_mode.chat).toBe(2);
    expect(s.by_mode.council).toBe(1);
    expect(s.patches_proposed_total).toBe(3);
  });

  it("picks earliest and latest ts", () => {
    const s = summarize([
      mk({ ts: "2026-04-15T10:00:00Z" }),
      mk({ ts: "2026-04-19T12:00:00Z" }),
      mk({ ts: "2026-04-17T08:00:00Z" }),
    ]);
    expect(s.first_ts).toBe("2026-04-15T10:00:00Z");
    expect(s.last_ts).toBe("2026-04-19T12:00:00Z");
  });
});
