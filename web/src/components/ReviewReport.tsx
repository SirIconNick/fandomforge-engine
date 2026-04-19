"use client";

import { useState } from "react";
import type { PostRenderReview } from "@/lib/types/generated";

type Verdict = PostRenderReview["overall_verdict"];
type DimensionRow = PostRenderReview["dimensions"][number];

const VERDICT_COLOR: Record<Verdict, string> = {
  pass: "text-green-400 border-green-500/40 bg-green-500/5",
  warn: "text-yellow-400 border-yellow-500/40 bg-yellow-500/5",
  fail: "text-red-400 border-red-500/40 bg-red-500/5",
};

const GRADE_COLOR: Record<string, string> = {
  "A+": "text-green-400",
  A: "text-green-400",
  "A-": "text-green-400",
  "B+": "text-cyan-400",
  B: "text-cyan-400",
  "B-": "text-cyan-400",
  "C+": "text-yellow-400",
  C: "text-yellow-400",
  "C-": "text-yellow-400",
  "D+": "text-red-400",
  D: "text-red-400",
  "D-": "text-red-400",
  F: "text-red-500",
};

const DIMENSION_ORDER: Array<DimensionRow["name"]> = [
  "technical",
  "visual",
  "audio",
  "structural",
  "shot_list",
];

function DimensionCard({ dim }: { dim: DimensionRow }) {
  const tone = VERDICT_COLOR[dim.verdict];
  return (
    <article className={`border rounded p-4 space-y-2 ${tone}`}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-serif text-lg capitalize">{dim.name.replace("_", " ")}</h3>
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm opacity-70">{dim.score.toFixed(0)}/100</span>
          <span className="px-2 py-1 rounded text-xs uppercase bg-black/30">{dim.verdict}</span>
        </div>
      </div>
      {dim.findings && dim.findings.length > 0 && (
        <ul className="text-sm space-y-1 text-white/85 list-disc ml-5">
          {dim.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {dim.measurements && Object.keys(dim.measurements).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-white/60">measurements</summary>
          <pre className="whitespace-pre-wrap text-white/70 mt-2 bg-black/30 p-2 rounded">
            {JSON.stringify(dim.measurements, null, 2)}
          </pre>
        </details>
      )}
    </article>
  );
}

export function ReviewReport({
  slug,
  initialReport,
}: {
  slug: string;
  initialReport: PostRenderReview | null;
}) {
  const [report, setReport] = useState<PostRenderReview | null>(initialReport);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>("");

  async function rerun() {
    setRunning(true);
    setError("");
    try {
      const res = await fetch(`/api/project/${slug}/review`, { method: "POST" });
      const body = (await res.json()) as {
        ok: boolean;
        report: PostRenderReview | null;
        stderr?: string;
      };
      if (body.report) setReport(body.report);
      if (!body.ok && !body.report) {
        setError(body.stderr ?? "review failed with no report");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "review request failed");
    } finally {
      setRunning(false);
    }
  }

  if (!report) {
    return (
      <div className="space-y-4">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1>Post-render review — {slug}</h1>
            <p className="text-sm text-white/60">
              No review has run for this project yet. Render + grade, then come back.
            </p>
          </div>
          <button
            onClick={rerun}
            disabled={running}
            className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold disabled:opacity-50"
          >
            {running ? "reviewing..." : "Run review"}
          </button>
        </header>
        {error && <div className="text-red-400 text-sm whitespace-pre-wrap">{error}</div>}
      </div>
    );
  }

  const gradeTone = GRADE_COLOR[report.grade] ?? "text-white";
  const overallTone = VERDICT_COLOR[report.overall_verdict];
  const dims = [...report.dimensions].sort((a, b) => {
    const ai = DIMENSION_ORDER.indexOf(a.name);
    const bi = DIMENSION_ORDER.indexOf(b.name);
    return ai - bi;
  });

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1>Post-render review — {slug}</h1>
          <p className="text-sm text-white/60 font-mono break-all">{report.video_path}</p>
          <p className="text-xs text-white/40">
            generated {new Date(report.generated_at).toLocaleString()}
          </p>
        </div>
        <button
          onClick={rerun}
          disabled={running}
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold disabled:opacity-50"
        >
          {running ? "reviewing..." : "Re-run"}
        </button>
      </header>

      <section className={`border rounded p-5 space-y-2 ${overallTone}`}>
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <div className="flex items-baseline gap-4">
            <span className={`font-serif text-6xl ${gradeTone}`}>{report.grade}</span>
            <span className="font-mono text-xl opacity-80">{report.score.toFixed(1)}/100</span>
          </div>
          <span className="px-3 py-1 rounded text-sm uppercase bg-black/30">
            {report.overall_verdict}
          </span>
        </div>
        <p className="text-sm">{report.ship_recommendation}</p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {dims.map((d) => (
          <DimensionCard key={d.name} dim={d} />
        ))}
      </section>

      {error && <div className="text-red-400 text-sm whitespace-pre-wrap">{error}</div>}
    </div>
  );
}

export default ReviewReport;
