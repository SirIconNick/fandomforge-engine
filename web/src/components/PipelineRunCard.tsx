"use client";

import { useState, useRef } from "react";

export interface PipelineProposal {
  tool_use_id: string;
  expert_slug: string;
  command: string;
  rationale: string;
  estimated_duration_seconds?: number;
}

interface RunEvent {
  type: "stdout" | "stderr" | "start" | "done" | "error";
  text?: string;
  exit_code?: number;
  killed_by_timeout?: boolean;
  message?: string;
}

export default function PipelineRunCard({
  projectSlug,
  proposal,
  onFinished,
}: {
  projectSlug: string;
  proposal: PipelineProposal;
  onFinished?: (exitCode: number) => void;
}) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error" | "rejected">(
    "idle"
  );
  const [lines, setLines] = useState<Array<{ stream: "out" | "err"; text: string }>>([]);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [errorText, setErrorText] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const longRun =
    proposal.estimated_duration_seconds != null &&
    proposal.estimated_duration_seconds > 30;

  async function run() {
    if (status !== "idle") return;
    setStatus("running");
    setLines([]);
    setExitCode(null);
    setTimedOut(false);
    setErrorText("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch("/api/pipeline/step", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_slug: projectSlug,
          command: proposal.command,
          rationale: proposal.rationale,
          expert_slug: proposal.expert_slug,
          estimated_duration_seconds: proposal.estimated_duration_seconds,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        setStatus("error");
        setErrorText(`server error ${res.status}: ${text}`);
        return;
      }

      if (!res.body) {
        setStatus("error");
        setErrorText("No response body from pipeline route.");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);

          const eventLine = raw.split("\n").find((l) => l.startsWith("event:"));
          const dataLine = raw.split("\n").find((l) => l.startsWith("data:"));
          if (!eventLine || !dataLine) continue;

          const event = eventLine.slice(6).trim() as RunEvent["type"];
          let data: RunEvent;
          try {
            data = JSON.parse(dataLine.slice(5).trim()) as RunEvent;
          } catch {
            continue;
          }

          if (event === "stdout" && data.text) {
            setLines((prev) => [...prev, { stream: "out", text: data.text ?? "" }]);
          } else if (event === "stderr" && data.text) {
            setLines((prev) => [...prev, { stream: "err", text: data.text ?? "" }]);
          } else if (event === "done") {
            setExitCode(data.exit_code ?? -1);
            setTimedOut(Boolean(data.killed_by_timeout));
            setStatus("done");
            onFinished?.(data.exit_code ?? -1);
          } else if (event === "error") {
            setErrorText(data.message ?? "unknown error");
            setStatus("error");
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setStatus("error");
        setErrorText((err as Error).message);
      }
    }
  }

  function cancel() {
    abortRef.current?.abort();
    setStatus("rejected");
  }

  return (
    <div className="rounded border border-blue-500/30 bg-blue-500/5 p-3 my-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded">
            proposed run
          </span>
          <span className="text-xs text-white/60">by {proposal.expert_slug}</span>
        </div>
        {proposal.estimated_duration_seconds != null && (
          <span className="text-[10px] text-white/40">
            ~{proposal.estimated_duration_seconds}s
          </span>
        )}
      </div>

      <div className="bg-black/40 border border-white/10 rounded p-2 mb-2 text-xs font-mono">
        <span className="text-white/40">$ ff</span>{" "}
        <span className="text-blue-200">{proposal.command}</span>
      </div>

      {proposal.rationale && (
        <p className="text-xs text-white/70 mb-2 whitespace-pre-wrap">
          {proposal.rationale}
        </p>
      )}

      {longRun && (
        <div className="text-xs text-yellow-300 bg-yellow-500/10 border border-yellow-500/30 rounded p-2 mb-2">
          This run is estimated over 30s. The inline runner is capped at 30s — open the{" "}
          <a href="/pipeline" className="underline">
            pipeline page
          </a>{" "}
          to start it.
        </div>
      )}

      {lines.length > 0 && (
        <pre className="bg-black/50 border border-white/10 rounded p-2 text-[10px] max-h-64 overflow-auto whitespace-pre-wrap mb-2">
          {lines.map((l, i) => (
            <span key={i} className={l.stream === "err" ? "text-red-300" : "text-white/80"}>
              {l.text}
            </span>
          ))}
        </pre>
      )}

      {errorText && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 mb-2">
          {errorText}
        </div>
      )}

      <div className="flex items-center gap-2">
        {status === "idle" && !longRun && (
          <>
            <button
              onClick={run}
              className="px-3 py-1.5 rounded bg-blue-500 text-black font-semibold text-xs"
            >
              Run
            </button>
            <button
              onClick={() => setStatus("rejected")}
              className="px-3 py-1.5 rounded border border-white/20 text-xs"
            >
              Dismiss
            </button>
          </>
        )}
        {status === "running" && (
          <button
            onClick={cancel}
            className="px-3 py-1.5 rounded bg-red-500/20 border border-red-500/40 text-red-300 text-xs"
          >
            Cancel
          </button>
        )}
        {status === "done" && exitCode === 0 && (
          <span className="text-xs text-green-300">Exit 0. Journal updated.</span>
        )}
        {status === "done" && exitCode !== 0 && (
          <span className="text-xs text-red-300">
            Exit {exitCode}
            {timedOut ? " (killed after 30s timeout)" : ""}.
          </span>
        )}
        {status === "rejected" && (
          <span className="text-xs text-white/50">Dismissed.</span>
        )}
      </div>
    </div>
  );
}
