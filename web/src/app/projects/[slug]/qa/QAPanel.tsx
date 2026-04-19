"use client";

import { useState } from "react";
import type { QaReport } from "@/lib/types/generated";

type RuleRow = QaReport["rules"][number];

const LEVEL_COLOR: Record<RuleRow["level"], string> = {
  block: "text-red-400 border-red-500/40",
  warn: "text-yellow-400 border-yellow-500/40",
  info: "text-sky-400 border-sky-500/40",
};
const STATUS_COLOR: Record<RuleRow["status"], string> = {
  pass: "bg-green-500/10 text-green-300",
  warn: "bg-yellow-500/10 text-yellow-300",
  fail: "bg-red-500/10 text-red-300",
  skipped: "bg-white/5 text-white/50",
  overridden: "bg-cyan-500/10 text-cyan-300",
};

export default function QAPanel({
  slug,
  initialReport,
}: {
  slug: string;
  initialReport: unknown;
}) {
  const [report, setReport] = useState<QaReport | null>((initialReport as QaReport) ?? null);
  const [running, setRunning] = useState(false);
  const [stdout, setStdout] = useState<string>("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  async function rerun() {
    setRunning(true);
    setStdout("");
    const res = await fetch(`/api/project/${slug}/qa`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ overrides }),
    });
    const body = (await res.json()) as {
      report?: QaReport;
      stdout?: string;
      stderr?: string;
    };
    if (body.report) setReport(body.report);
    setStdout([body.stdout ?? "", body.stderr ?? ""].filter(Boolean).join("\n"));
    setRunning(false);
  }

  const summary = report?.summary;
  const statusColor: Record<string, string> = {
    pass: "text-green-400",
    warn: "text-yellow-400",
    fail: "text-red-400",
  };

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1>QA gate — {slug}</h1>
          {report ? (
            <p className={`text-2xl font-serif ${statusColor[report.status] ?? "text-white"}`}>
              {report.status.toUpperCase()}
            </p>
          ) : (
            <p className="text-white/60">No qa-report.json yet. Click run to generate one.</p>
          )}
        </div>
        <button
          onClick={rerun}
          disabled={running}
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold disabled:opacity-50"
        >
          {running ? "running..." : "Run gate"}
        </button>
      </header>

      {summary && (
        <div className="grid grid-cols-5 gap-2 text-sm">
          <Stat label="Total" value={summary.total ?? 0} />
          <Stat label="Passed" value={summary.passed ?? 0} tone="text-green-400" />
          <Stat label="Warned" value={summary.warned ?? 0} tone="text-yellow-400" />
          <Stat label="Failed" value={summary.failed ?? 0} tone="text-red-400" />
          <Stat label="Overridden" value={summary.overridden ?? 0} tone="text-cyan-400" />
        </div>
      )}

      {report?.rules?.map((rule) => (
        <article
          key={rule.id}
          className={`border rounded p-4 space-y-2 ${LEVEL_COLOR[rule.level]}`}
        >
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="font-mono text-xs opacity-70">{rule.id}</div>
              <div className="text-lg font-serif">{rule.name}</div>
            </div>
            <div className={`px-2 py-1 rounded text-xs uppercase ${STATUS_COLOR[rule.status]}`}>
              {rule.status}
            </div>
          </div>
          {rule.message && <p className="text-sm text-white/80">{rule.message}</p>}
          {rule.evidence && Object.keys(rule.evidence).length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-white/60">evidence</summary>
              <pre className="whitespace-pre-wrap text-white/70 mt-2 bg-black/30 p-2 rounded">
                {JSON.stringify(rule.evidence, null, 2)}
              </pre>
            </details>
          )}

          {rule.status === "fail" && rule.level === "block" && (
            <div className="flex items-center gap-2">
              <input
                className="flex-1 bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
                placeholder="reason for override"
                value={overrides[rule.id] ?? ""}
                onChange={(e) =>
                  setOverrides((prev) => ({ ...prev, [rule.id]: e.target.value }))
                }
              />
              <span className="text-xs text-white/50">
                (override applied on next run)
              </span>
            </div>
          )}
          {rule.status === "overridden" && rule.override_reason && (
            <p className="text-xs text-cyan-300">override: {rule.override_reason}</p>
          )}
        </article>
      ))}

      {stdout && (
        <details className="text-xs">
          <summary className="cursor-pointer text-white/60">CLI output</summary>
          <pre className="whitespace-pre-wrap text-white/60 mt-2 bg-black/40 p-3 rounded">
            {stdout}
          </pre>
        </details>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="border border-white/10 rounded p-3">
      <div className="text-[10px] uppercase tracking-wide text-white/50">{label}</div>
      <div className={`text-2xl font-serif ${tone ?? "text-white"}`}>{value}</div>
    </div>
  );
}
