"use client";

import { useState } from "react";

interface ActionResult {
  ok: boolean;
  stdout?: string;
  stderr?: string;
  output_path?: string;
  outputs?: string[];
  bytes?: number;
  exitCode?: number;
}

type ActionKey = "render" | "color" | "export-nle";

const LABELS: Record<ActionKey, string> = {
  render: "Render rough cut",
  color: "Apply color grade",
  "export-nle": "Export FCPXML/EDL",
};

const RUNNING_LABELS: Record<ActionKey, string> = {
  render: "rendering…",
  color: "grading…",
  "export-nle": "exporting…",
};

export function ProjectActions({ slug }: { slug: string }) {
  const [busy, setBusy] = useState<ActionKey | null>(null);
  const [result, setResult] = useState<(ActionResult & { action: ActionKey }) | null>(null);

  async function run(action: ActionKey) {
    setBusy(action);
    setResult(null);
    try {
      const res = await fetch(`/api/project/${slug}/${action}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      });
      const body = (await res.json()) as ActionResult;
      setResult({ ...body, action });
    } catch (e) {
      setResult({
        ok: false,
        stderr: e instanceof Error ? e.message : String(e),
        action,
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {(Object.keys(LABELS) as ActionKey[]).map((key) => (
          <button
            key={key}
            onClick={() => run(key)}
            disabled={busy !== null}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50 disabled:opacity-40 text-sm"
          >
            {busy === key ? RUNNING_LABELS[key] : LABELS[key]}
          </button>
        ))}
      </div>

      {result && (
        <div
          className={`border rounded p-3 text-sm ${
            result.ok
              ? "border-green-500/40 bg-green-500/5"
              : "border-red-500/40 bg-red-500/5"
          }`}
        >
          <div className="font-mono text-xs mb-2">
            {result.ok ? "✓" : "✗"} {result.action}
            {result.exitCode !== undefined && ` (exit ${result.exitCode})`}
          </div>
          {result.output_path && (
            <div className="text-xs text-white/70 font-mono break-all">
              → {result.output_path}
              {result.bytes !== undefined && ` (${(result.bytes / 1024 / 1024).toFixed(1)} MB)`}
            </div>
          )}
          {result.outputs && result.outputs.length > 0 && (
            <ul className="text-xs text-white/70 font-mono list-disc ml-5">
              {result.outputs.map((p) => (
                <li key={p} className="break-all">{p}</li>
              ))}
            </ul>
          )}
          {(result.stdout || result.stderr) && (
            <details className="mt-2 text-xs">
              <summary className="cursor-pointer text-white/60">output</summary>
              <pre className="whitespace-pre-wrap text-white/70 mt-2 bg-black/30 p-2 rounded max-h-60 overflow-auto">
                {(result.stdout ?? "") + (result.stderr ? `\n--- stderr ---\n${result.stderr}` : "")}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default ProjectActions;
