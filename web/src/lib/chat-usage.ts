import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import { appendJsonLine, readJsonLines } from "@/lib/atomic-write";

export interface ChatUsageEntry {
  ts: string;
  expert_slug: string;
  project_slug: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  stop_reason: string | null;
  patches_proposed: number;
  pipeline_runs_proposed: number;
  mode: "chat" | "council";
}

const USAGE_FILE = path.join(PROJECT_ROOT, ".history", "chat-usage.jsonl");

export async function recordChatUsage(entry: ChatUsageEntry): Promise<void> {
  try {
    await appendJsonLine(USAGE_FILE, entry);
  } catch {
    /* journal write is best-effort — never fail a chat because logging fell over */
  }
}

export async function readChatUsage(): Promise<ChatUsageEntry[]> {
  const entries = (await readJsonLines(USAGE_FILE)) as ChatUsageEntry[];
  return entries;
}

export interface UsageStats {
  total_turns: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation: number;
  total_cache_read: number;
  cache_hit_rate: number;
  by_expert: Record<
    string,
    {
      turns: number;
      input_tokens: number;
      output_tokens: number;
      cache_read: number;
    }
  >;
  by_mode: Record<"chat" | "council", number>;
  patches_proposed_total: number;
  pipeline_runs_proposed_total: number;
  first_ts: string | null;
  last_ts: string | null;
}

export function summarize(entries: ChatUsageEntry[]): UsageStats {
  const stats: UsageStats = {
    total_turns: entries.length,
    total_input_tokens: 0,
    total_output_tokens: 0,
    total_cache_creation: 0,
    total_cache_read: 0,
    cache_hit_rate: 0,
    by_expert: {},
    by_mode: { chat: 0, council: 0 },
    patches_proposed_total: 0,
    pipeline_runs_proposed_total: 0,
    first_ts: null,
    last_ts: null,
  };

  for (const e of entries) {
    stats.total_input_tokens += e.input_tokens;
    stats.total_output_tokens += e.output_tokens;
    stats.total_cache_creation += e.cache_creation_input_tokens;
    stats.total_cache_read += e.cache_read_input_tokens;
    stats.patches_proposed_total += e.patches_proposed;
    stats.pipeline_runs_proposed_total += e.pipeline_runs_proposed;
    stats.by_mode[e.mode] = (stats.by_mode[e.mode] ?? 0) + 1;

    const by = (stats.by_expert[e.expert_slug] ??= {
      turns: 0,
      input_tokens: 0,
      output_tokens: 0,
      cache_read: 0,
    });
    by.turns += 1;
    by.input_tokens += e.input_tokens;
    by.output_tokens += e.output_tokens;
    by.cache_read += e.cache_read_input_tokens;

    if (stats.first_ts === null || e.ts < stats.first_ts) stats.first_ts = e.ts;
    if (stats.last_ts === null || e.ts > stats.last_ts) stats.last_ts = e.ts;
  }

  const cacheDenom = stats.total_input_tokens + stats.total_cache_read;
  stats.cache_hit_rate = cacheDenom > 0 ? stats.total_cache_read / cacheDenom : 0;
  return stats;
}
