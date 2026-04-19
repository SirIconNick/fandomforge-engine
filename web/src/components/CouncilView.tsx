"use client";

import { useEffect, useMemo, useState } from "react";
import ArtifactDiffReview from "@/components/ArtifactDiffReview";
import type { Operation } from "fast-json-patch";

interface ExpertResponse {
  expert_slug: string;
  ok: boolean;
  reply: string;
  patches: Array<{
    tool_use_id: string;
    expert_slug: string;
    artifact: string;
    rationale: string;
    patch: Operation[];
  }>;
  error: string | null;
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
  } | null;
}

interface Conflict {
  artifact: string;
  path: string;
  proposers: string[];
}

interface Expert {
  slug: string;
  name: string;
  color: string;
}

export default function CouncilView({
  experts,
  initialProject,
}: {
  experts: Expert[];
  initialProject?: string;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [project, setProject] = useState(initialProject ?? "");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [responses, setResponses] = useState<ExpertResponse[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [hasKey, setHasKey] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/env")
      .then((r) => r.json())
      .then((d: { has_anthropic_key: boolean }) =>
        setHasKey(Boolean(d.has_anthropic_key))
      )
      .catch(() => setHasKey(null));
  }, []);

  function toggle(slug: string) {
    setSelected((prev) => {
      if (prev.includes(slug)) return prev.filter((s) => s !== slug);
      if (prev.length >= 4) return prev;
      return [...prev, slug];
    });
  }

  async function ask() {
    if (selected.length < 2 || !question.trim()) return;
    setLoading(true);
    setError("");
    setResponses([]);
    setConflicts([]);
    try {
      const res = await fetch("/api/experts/council", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          expert_slugs: selected,
          project_slug: project || undefined,
          question,
        }),
      });
      const body = await res.json();
      if (!res.ok || body.error) {
        setError(body.error ?? `council failed (${res.status})`);
      } else {
        setResponses(body.responses ?? []);
        setConflicts(body.conflicts ?? []);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const conflictKeys = useMemo(
    () =>
      new Set(
        conflicts.flatMap((c) => c.proposers.map((p) => `${p}::${c.artifact}::${c.path}`))
      ),
    [conflicts]
  );

  return (
    <div className="space-y-6">
      {hasKey === false && (
        <div className="text-sm border border-yellow-500/40 bg-yellow-500/10 rounded p-3">
          <div className="font-semibold text-yellow-300">ANTHROPIC_API_KEY not set</div>
          <div className="text-yellow-200/80 text-xs mt-1">
            Add it to web/.env.local and restart pnpm dev.
          </div>
        </div>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Pick 2–4 experts</h2>
        <div className="flex flex-wrap gap-2">
          {experts.map((e) => {
            const active = selected.includes(e.slug);
            return (
              <button
                key={e.slug}
                onClick={() => toggle(e.slug)}
                className={`px-3 py-1.5 rounded border text-sm ${
                  active
                    ? "bg-[var(--color-forge,#ff5a1f)] text-black border-[var(--color-forge,#ff5a1f)] font-semibold"
                    : "border-white/15 text-white/70 hover:border-white/40"
                }`}
              >
                {e.slug}
              </button>
            );
          })}
        </div>
        <p className="text-xs text-white/50">
          {selected.length} selected (min 2, max 4).
        </p>
      </section>

      <section className="space-y-2">
        <label className="block text-sm text-white/70">
          Project slug <span className="text-white/40">(optional)</span>
        </label>
        <input
          value={project}
          onChange={(e) => setProject(e.target.value)}
          placeholder="my-multifandom-edit"
          className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 text-sm font-mono"
        />
      </section>

      <section className="space-y-2">
        <label className="block text-sm text-white/70">Question for the council</label>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="How should we handle the transition between act 2 and act 3?"
          className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 min-h-24 text-sm"
        />
        <button
          onClick={ask}
          disabled={loading || selected.length < 2 || !question.trim()}
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold disabled:opacity-40"
        >
          {loading ? "Asking…" : "Ask the council"}
        </button>
      </section>

      {error && (
        <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-3">
          {error}
        </div>
      )}

      {conflicts.length > 0 && (
        <section className="space-y-2 border border-orange-500/40 bg-orange-500/5 rounded p-3">
          <h3 className="text-sm font-semibold text-orange-300">
            {conflicts.length} conflict{conflicts.length === 1 ? "" : "s"} between
            proposals
          </h3>
          <p className="text-xs text-orange-200/70">
            These experts proposed edits to the same JSON path. Accept at most one per
            conflict to avoid clobbering each other.
          </p>
          <ul className="text-xs space-y-1">
            {conflicts.map((c, i) => (
              <li key={i} className="font-mono">
                <span className="text-orange-200">{c.artifact}</span>
                <span className="text-white/40">{c.path}</span>
                <span className="text-white/60"> — {c.proposers.join(" vs ")}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {responses.length > 0 && (
        <section className="grid md:grid-cols-2 gap-4">
          {responses.map((r) => (
            <div
              key={r.expert_slug}
              className="border border-white/10 rounded p-3 bg-white/[0.02]"
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold text-sm">{r.expert_slug}</h3>
                {r.usage && (
                  <span className="text-[10px] text-white/40">
                    {r.usage.input_tokens} in / {r.usage.output_tokens} out
                  </span>
                )}
              </div>
              {r.error ? (
                <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">
                  {r.error}
                </div>
              ) : (
                <div className="text-sm whitespace-pre-wrap">{r.reply}</div>
              )}
              {r.patches.map((p) => {
                const conflictKey = `${r.expert_slug}::${p.artifact}::${p.patch[0]?.path ?? ""}`;
                const inConflict = conflictKeys.has(conflictKey);
                return (
                  <div
                    key={p.tool_use_id}
                    className={inConflict ? "ring-1 ring-orange-500/50 rounded" : ""}
                  >
                    {project ? (
                      <ArtifactDiffReview projectSlug={project} patch={p} />
                    ) : (
                      <div className="text-xs text-yellow-300 bg-yellow-500/10 border border-yellow-500/30 rounded p-2 my-2">
                        Patch proposed against {p.artifact} but no project slug is set.
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
