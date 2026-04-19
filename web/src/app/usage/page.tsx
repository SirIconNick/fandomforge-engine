import { readChatUsage, summarize } from "@/lib/chat-usage";

export const dynamic = "force-dynamic";

function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function fmt(n: number): string {
  return n.toLocaleString();
}

export default async function UsagePage() {
  const entries = await readChatUsage();
  const stats = summarize(entries);
  const recent = entries.slice(-20).reverse();

  const expertRows = Object.entries(stats.by_expert).sort(
    (a, b) => b[1].turns - a[1].turns
  );

  return (
    <div className="space-y-6">
      <div>
        <h1>Chat usage</h1>
        <p className="text-sm text-white/60 max-w-2xl">
          Every expert chat and council turn is logged to{" "}
          <code className="font-mono text-xs">.history/chat-usage.jsonl</code> with token
          counts and cache hits. These numbers tell you whether prompt caching is paying
          off (expect cache-read to climb after turn 2 on any single chat).
        </p>
      </div>

      {stats.total_turns === 0 ? (
        <div className="border border-dashed border-white/15 rounded p-8 text-center text-white/60 text-sm">
          No chat usage recorded yet. Run a turn on an expert to populate this page.
        </div>
      ) : (
        <>
          <div className="grid sm:grid-cols-2 md:grid-cols-4 gap-3">
            <Tile label="Total turns" value={fmt(stats.total_turns)} />
            <Tile
              label="Cache hit rate"
              value={pct(stats.cache_hit_rate)}
              hint={`${fmt(stats.total_cache_read)} / ${fmt(stats.total_input_tokens + stats.total_cache_read)} input tokens from cache`}
            />
            <Tile
              label="Input tokens"
              value={fmt(stats.total_input_tokens + stats.total_cache_read + stats.total_cache_creation)}
              hint={`${fmt(stats.total_input_tokens)} direct · ${fmt(stats.total_cache_read)} cached · ${fmt(stats.total_cache_creation)} cache writes`}
            />
            <Tile label="Output tokens" value={fmt(stats.total_output_tokens)} />
            <Tile
              label="Patches proposed"
              value={fmt(stats.patches_proposed_total)}
            />
            <Tile
              label="Pipeline runs proposed"
              value={fmt(stats.pipeline_runs_proposed_total)}
            />
            <Tile
              label="Chat / Council"
              value={`${fmt(stats.by_mode.chat)} · ${fmt(stats.by_mode.council)}`}
            />
            <Tile
              label="Session span"
              value={
                stats.first_ts && stats.last_ts
                  ? `${stats.first_ts.slice(0, 10)} → ${stats.last_ts.slice(0, 10)}`
                  : "—"
              }
            />
          </div>

          <section className="space-y-2">
            <h2>By expert</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-white/50 text-xs uppercase tracking-wide">
                    <th className="pb-2">Expert</th>
                    <th className="pb-2 text-right">Turns</th>
                    <th className="pb-2 text-right">Input</th>
                    <th className="pb-2 text-right">Output</th>
                    <th className="pb-2 text-right">Cache read</th>
                  </tr>
                </thead>
                <tbody className="text-xs font-mono">
                  {expertRows.map(([expert, s]) => (
                    <tr key={expert} className="border-t border-white/10">
                      <td className="py-1.5">{expert}</td>
                      <td className="text-right">{fmt(s.turns)}</td>
                      <td className="text-right">{fmt(s.input_tokens)}</td>
                      <td className="text-right">{fmt(s.output_tokens)}</td>
                      <td className="text-right">{fmt(s.cache_read)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="space-y-2">
            <h2>Recent turns</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-white/50 text-xs uppercase tracking-wide">
                    <th className="pb-2">When</th>
                    <th className="pb-2">Expert</th>
                    <th className="pb-2">Project</th>
                    <th className="pb-2">Mode</th>
                    <th className="pb-2 text-right">In</th>
                    <th className="pb-2 text-right">Cache</th>
                    <th className="pb-2 text-right">Out</th>
                    <th className="pb-2 text-right">Patches</th>
                  </tr>
                </thead>
                <tbody className="text-xs font-mono">
                  {recent.map((e, i) => (
                    <tr key={i} className="border-t border-white/10">
                      <td className="py-1.5">{e.ts.replace("T", " ").slice(0, 19)}</td>
                      <td>{e.expert_slug}</td>
                      <td className="text-white/60">{e.project_slug ?? "—"}</td>
                      <td className="text-white/60">{e.mode}</td>
                      <td className="text-right">{fmt(e.input_tokens)}</td>
                      <td className="text-right">{fmt(e.cache_read_input_tokens)}</td>
                      <td className="text-right">{fmt(e.output_tokens)}</td>
                      <td className="text-right">{e.patches_proposed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="border border-white/10 rounded p-3 bg-white/[0.02]">
      <div className="text-[10px] uppercase tracking-wide text-white/50">{label}</div>
      <div className="text-xl font-semibold mt-1">{value}</div>
      {hint && <div className="text-[10px] text-white/40 mt-1">{hint}</div>}
    </div>
  );
}
