"use client";

import { useState, useRef, useEffect } from "react";
import ArtifactDiffReview from "@/components/ArtifactDiffReview";
import PipelineRunCard, { type PipelineProposal } from "@/components/PipelineRunCard";
import type { Operation } from "fast-json-patch";

interface ProposedPatch {
  tool_use_id: string;
  expert_slug: string;
  artifact: string;
  rationale: string;
  patch: Operation[];
}

interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  patches?: ProposedPatch[];
  pipeline_runs?: PipelineProposal[];
  usage?: {
    input_tokens: number;
    output_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
  };
}

export default function ExpertChat({
  expertSlug,
  projectSlug,
}: {
  expertSlug: string;
  projectSlug?: string;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>("");
  const [hasKey, setHasKey] = useState<boolean | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fetch("/api/env")
      .then((r) => r.json())
      .then((d: { has_anthropic_key: boolean }) =>
        setHasKey(Boolean(d.has_anthropic_key))
      )
      .catch(() => setHasKey(null));
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({
      top: listRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns]);

  async function send() {
    const trimmed = input.trim();
    if (!trimmed || sending) return;
    setError("");
    const userTurn: ChatTurn = { role: "user", content: trimmed };
    const nextTurns: ChatTurn[] = [...turns, userTurn];
    setTurns(nextTurns);
    setInput("");
    setSending(true);

    try {
      const res = await fetch("/api/experts/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          expert_slug: expertSlug,
          project_slug: projectSlug,
          messages: nextTurns.map((t) => ({ role: t.role, content: t.content })),
        }),
      });
      const body = (await res.json()) as {
        reply?: string;
        patches?: ProposedPatch[];
        pipeline_runs?: PipelineProposal[];
        usage?: ChatTurn["usage"];
        error?: string;
      };
      if (!res.ok || body.error) {
        setError(body.error ?? `chat failed (${res.status})`);
      } else {
        setTurns((prev) => [
          ...prev,
          {
            role: "assistant",
            content: body.reply ?? "",
            patches: body.patches ?? [],
            pipeline_runs: body.pipeline_runs ?? [],
            usage: body.usage,
          },
        ]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-220px)] min-h-[400px]">
      {hasKey === false && (
        <div className="mb-3 text-sm border border-yellow-500/40 bg-yellow-500/10 rounded p-3">
          <div className="font-semibold text-yellow-300">ANTHROPIC_API_KEY not set</div>
          <div className="text-yellow-200/80 text-xs mt-1">
            Add{" "}
            <code className="font-mono bg-black/30 px-1 rounded">
              ANTHROPIC_API_KEY=sk-ant-…
            </code>{" "}
            to <code className="font-mono bg-black/30 px-1 rounded">web/.env.local</code>{" "}
            and restart{" "}
            <code className="font-mono bg-black/30 px-1 rounded">pnpm dev</code> to enable
            expert chat.
          </div>
        </div>
      )}
      <div
        ref={listRef}
        className="flex-1 overflow-y-auto space-y-3 border border-white/10 rounded p-4 bg-black/20"
      >
        {turns.length === 0 && (
          <p className="text-white/50 text-sm">
            Ask anything. The expert has your current project artifacts loaded as context
            and can propose structured edits that you review before anything is written.
          </p>
        )}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={`rounded p-3 ${
              turn.role === "user"
                ? "bg-[var(--color-forge,#ff5a1f)]/10 border border-[var(--color-forge,#ff5a1f)]/30 ml-6"
                : "bg-white/5 border border-white/10 mr-6"
            }`}
          >
            <div className="text-[10px] uppercase tracking-wide text-white/40 mb-1 flex items-center gap-2">
              <span>{turn.role}</span>
              {turn.usage && (
                <span className="text-white/30">
                  · {turn.usage.input_tokens} in / {turn.usage.output_tokens} out ·{" "}
                  cache read {turn.usage.cache_read_input_tokens}
                </span>
              )}
            </div>
            {turn.content && (
              <div className="whitespace-pre-wrap text-sm">{turn.content}</div>
            )}
            {turn.patches && turn.patches.length > 0 && projectSlug && (
              <div className="mt-2">
                {turn.patches.map((p) => (
                  <ArtifactDiffReview
                    key={p.tool_use_id}
                    projectSlug={projectSlug}
                    patch={p}
                  />
                ))}
              </div>
            )}
            {turn.pipeline_runs && turn.pipeline_runs.length > 0 && projectSlug && (
              <div className="mt-2">
                {turn.pipeline_runs.map((p) => (
                  <PipelineRunCard
                    key={p.tool_use_id}
                    projectSlug={projectSlug}
                    proposal={p}
                  />
                ))}
              </div>
            )}
            {((turn.patches && turn.patches.length > 0) ||
              (turn.pipeline_runs && turn.pipeline_runs.length > 0)) &&
              !projectSlug && (
                <div className="mt-2 text-xs text-yellow-300 bg-yellow-500/10 border border-yellow-500/30 rounded p-2">
                  This turn proposed edits or runs, but no project is loaded. Open the
                  chat from a project context to act on them.
                </div>
              )}
          </div>
        ))}
      </div>

      {error && (
        <div className="mt-3 text-sm text-red-300 border border-red-500/30 rounded p-2 bg-red-500/10">
          {error}
        </div>
      )}

      <div className="mt-3 flex items-end gap-2">
        <textarea
          className="flex-1 bg-black/30 border border-white/10 rounded px-3 py-2 min-h-14 max-h-48 resize-y text-sm"
          placeholder="Ask…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold disabled:opacity-50"
          onClick={send}
          disabled={sending || !input.trim()}
        >
          {sending ? "..." : "Send"}
        </button>
      </div>
      <p className="text-[10px] text-white/40 mt-1">cmd/ctrl+enter to send</p>
    </div>
  );
}
