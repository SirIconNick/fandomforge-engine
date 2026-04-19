"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

export interface AutopilotEvent {
  ts: string;
  run_id: string;
  step_id: string;
  status: "started" | "ok" | "skipped" | "failed" | "ended";
  message: string;
  evidence?: Record<string, unknown>;
  duration_sec?: number;
}

interface Estimate {
  estimated_wall_time_sec?: number;
  estimated_cost_usd?: number;
  source_count?: number;
  notes?: string;
}

const STEP_ORDER = [
  "scaffold",
  "copy_song",
  "ingest_sources",
  "beat_analyze",
  "edit_plan_stub",
  "propose_shots",
  "emotion_arc",
  "qa_gate",
  "roughcut",
  "color",
  "export",
  "post_render_review",
];

const STEP_LABELS: Record<string, string> = {
  scaffold: "Scaffold project directories",
  copy_song: "Copy song into assets/",
  ingest_sources: "Ingest source videos (scenes + transcript + catalog)",
  beat_analyze: "Run ff beat analyze",
  edit_plan_stub: "Draft edit plan (edit-strategist LLM if credits available, heuristic otherwise)",
  propose_shots: "Propose shot list",
  emotion_arc: "Infer emotion arc",
  qa_gate: "Run QA gate",
  roughcut: "Render rough cut (requires source videos)",
  color: "Apply color grade",
  export: "Export NLE XML (FCPXML)",
  post_render_review: "Grade the render (technical / visual / audio / structural / shot list)",
  _run: "Run",
};

export default function AutopilotProgress({
  projectSlug,
}: {
  projectSlug: string;
}) {
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [events, setEvents] = useState<AutopilotEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const eventSourceRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetch(`/api/autopilot/estimate?project=${encodeURIComponent(projectSlug)}`)
      .then((r) => r.json())
      .then((d) => setEstimate(d))
      .catch(() => setEstimate(null));
  }, [projectSlug]);

  async function startRun() {
    setEvents([]);
    setError("");
    setRunning(true);
    try {
      const res = await fetch("/api/autopilot/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_slug: projectSlug }),
      });
      const body = await res.json();
      if (!res.ok || !body.ok) {
        setError(body.error ?? `start failed (${res.status})`);
        setRunning(false);
        return;
      }
      streamEvents();
    } catch (e) {
      setError((e as Error).message);
      setRunning(false);
    }
  }

  function streamEvents() {
    const controller = new AbortController();
    eventSourceRef.current = controller;

    const url = `/api/autopilot/events?project=${encodeURIComponent(projectSlug)}`;
    fetch(url, { signal: controller.signal })
      .then(async (res) => {
        if (!res.body) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            const block = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
            if (!dataLine) continue;
            try {
              const event = JSON.parse(dataLine.slice(5).trim()) as AutopilotEvent;
              setEvents((prev) => [...prev, event]);
              if (event.step_id === "_run" && event.status === "ended") {
                setRunning(false);
                controller.abort();
              }
            } catch {
              /* skip malformed */
            }
          }
        }
      })
      .catch(() => setRunning(false));
  }

  const stepStatus: Record<string, AutopilotEvent | undefined> = {};
  for (const event of events) {
    if (event.step_id === "_run") continue;
    const prev = stepStatus[event.step_id];
    if (!prev || event.status !== "started") stepStatus[event.step_id] = event;
  }

  return (
    <div className="space-y-5">
      {estimate && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Tile label="Est. wall time" value={`${estimate.estimated_wall_time_sec ?? "—"}s`} />
          <Tile label="Est. cost" value={`$${estimate.estimated_cost_usd?.toFixed(3) ?? "0.000"}`} />
          <Tile label="Sources found" value={String(estimate.source_count ?? 0)} />
          <Tile
            label="Steps"
            value={`${STEP_ORDER.length}`}
            hint="scaffold → beat → plan → shots → emotion → QA"
          />
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={startRun}
          disabled={running}
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-sm disabled:opacity-40"
        >
          {running ? "Running…" : "Start Auto-pilot"}
        </button>
        {running && (
          <span className="text-xs text-white/60">
            streaming from .history/autopilot.jsonl
          </span>
        )}
      </div>

      {error && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-3 whitespace-pre-wrap">
          {error}
        </div>
      )}

      <section className="space-y-2">
        <h2>Steps</h2>
        <div className="space-y-2">
          {STEP_ORDER.map((step) => {
            const event = stepStatus[step];
            const label = STEP_LABELS[step] ?? step;
            const status = event?.status ?? "pending";
            const color =
              status === "ok" || status === "skipped" ? "text-green-300" :
              status === "started" ? "text-blue-300" :
              status === "failed" ? "text-red-300" : "text-white/40";
            const icon =
              status === "ok" ? "✓" :
              status === "skipped" ? "⟳" :
              status === "started" ? "…" :
              status === "failed" ? "✗" : "·";
            const showReviewLink =
              step === "post_render_review" && (status === "ok" || status === "skipped");
            return (
              <div
                key={step}
                className="flex items-start gap-3 border border-white/10 rounded p-2 bg-white/[0.02]"
              >
                <span className={`font-mono text-lg ${color}`}>{icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm flex items-center gap-2 flex-wrap">
                    <span>{label}</span>
                    {showReviewLink && (
                      <Link
                        href={`/projects/${projectSlug}/review`}
                        className="text-xs px-2 py-0.5 rounded border border-[var(--color-forge)]/40 text-[var(--color-forge)] hover:bg-[var(--color-forge)]/10"
                      >
                        open review →
                      </Link>
                    )}
                  </div>
                  {event?.message && (
                    <div className="text-xs text-white/60 mt-0.5">{event.message}</div>
                  )}
                  {event?.duration_sec != null && (
                    <div className="text-[10px] text-white/40 font-mono mt-0.5">
                      {event.duration_sec.toFixed(2)}s
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {events.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-white/60">
            Raw event stream ({events.length})
          </summary>
          <pre className="bg-black/40 border border-white/10 rounded p-2 mt-2 max-h-80 overflow-auto text-[10px] whitespace-pre-wrap">
            {events.map((e, i) => `${e.ts} [${e.status}] ${e.step_id}: ${e.message}`).join("\n")}
          </pre>
        </details>
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
