"use client";

import { useState } from "react";
import ArtifactDiffReview from "@/components/ArtifactDiffReview";
import type { Operation } from "fast-json-patch";

interface ProposedPatchLike {
  tool_use_id: string;
  expert_slug: string;
  artifact: string;
  rationale: string;
  patch: Operation[];
}

interface ProposerResponse {
  ok?: boolean;
  draft_summary?: { shot_count: number; generator: string };
  patch?: ProposedPatchLike;
  error?: string;
  stderr?: string;
  schema_errors?: unknown[];
}

export default function ProposeShotsClient({
  projectSlug,
}: {
  projectSlug: string;
}) {
  const [status, setStatus] = useState<"idle" | "running" | "ready" | "error">(
    "idle"
  );
  const [errorText, setErrorText] = useState("");
  const [response, setResponse] = useState<ProposerResponse | null>(null);

  async function runProposer() {
    setStatus("running");
    setErrorText("");
    setResponse(null);
    try {
      const res = await fetch("/api/experts/propose-shots", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_slug: projectSlug }),
      });
      const body = (await res.json()) as ProposerResponse;
      if (!res.ok || !body.ok) {
        setStatus("error");
        setErrorText(
          body.error ??
            `proposer failed (${res.status})` +
              (body.stderr ? `\n${body.stderr}` : "")
        );
        setResponse(body);
        return;
      }
      setResponse(body);
      setStatus("ready");
    } catch (err) {
      setStatus("error");
      setErrorText((err as Error).message);
    }
  }

  return (
    <div className="space-y-4">
      {status === "idle" && (
        <button
          onClick={runProposer}
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold"
        >
          Run shot-proposer
        </button>
      )}
      {status === "running" && (
        <div className="text-sm text-white/70">
          Running the proposer — typically &lt; 2 seconds.
        </div>
      )}
      {status === "error" && (
        <div className="space-y-2">
          <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-3 whitespace-pre-wrap">
            {errorText}
          </div>
          <button
            onClick={runProposer}
            className="px-3 py-1.5 rounded border border-white/20 text-xs"
          >
            Retry
          </button>
        </div>
      )}
      {status === "ready" && response?.patch && (
        <>
          <div className="text-xs text-white/60">
            Drafted {response.draft_summary?.shot_count ?? "?"} shots via{" "}
            <code className="font-mono">{response.draft_summary?.generator}</code>. Review
            below. Nothing writes to disk until you click Apply.
          </div>
          <ArtifactDiffReview
            projectSlug={projectSlug}
            patch={response.patch}
            onApplied={() => {
              setStatus("idle");
            }}
          />
          <button
            onClick={runProposer}
            className="px-3 py-1.5 rounded border border-white/20 text-xs"
          >
            Re-run proposer
          </button>
        </>
      )}
    </div>
  );
}
