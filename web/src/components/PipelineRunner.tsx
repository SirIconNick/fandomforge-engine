"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  project: string;
  availableShotLists: string[];
  availableSongs: string[];
  availableDialogueScripts: string[];
  availableColorPlans: string[];
  onComplete?: (exitCode: number | undefined, output: string) => void;
}

const COLOR_PRESETS = [
  "none",
  "tactical",
  "teal_orange",
  "desaturated_warm",
  "crushed_noir",
  "cool_cinematic",
  "nostalgic",
  "film_bleach",
];

export function PipelineRunner({
  project,
  availableShotLists,
  availableSongs,
  availableDialogueScripts,
  availableColorPlans,
  onComplete,
}: Props) {
  const [shotList, setShotList] = useState(availableShotLists[0] ?? "shot-list.md");
  const [song, setSong] = useState<string>("");
  const [dialogue, setDialogue] = useState<string>("");
  const [colorPlan, setColorPlan] = useState<string>("");
  const [colorPreset, setColorPreset] = useState<string>("none");
  const [output, setOutput] = useState("rough-cut.mp4");
  const [width, setWidth] = useState(1280);
  const [height, setHeight] = useState(720);
  const [fps, setFps] = useState(24);

  const [runId, setRunId] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "running" | "completed" | "failed">(
    "idle",
  );
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [log]);

  async function startRun() {
    setLog([]);
    setStatus("running");
    try {
      const res = await fetch("/api/pipeline/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          shotList,
          song: song || undefined,
          dialogue: dialogue || undefined,
          colorPlan: colorPlan || undefined,
          colorPreset,
          output,
          width,
          height,
          fps,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Failed to start");
      setRunId(data.id);
      streamRun(data.id);
    } catch (err) {
      setLog((l) => [...l, `[ERROR] ${String(err)}`]);
      setStatus("failed");
    }
  }

  function streamRun(id: string) {
    const es = new EventSource(`/api/pipeline/run/${id}/stream`);
    es.addEventListener("start", () => {
      setLog((l) => [...l, `[started] ${id}`]);
    });
    es.addEventListener("log", (e) => {
      try {
        const { line } = JSON.parse((e as MessageEvent).data);
        setLog((l) => [...l, line]);
      } catch {}
    });
    es.addEventListener("done", (e) => {
      try {
        const { status: s, exitCode } = JSON.parse((e as MessageEvent).data);
        setStatus(s === "completed" ? "completed" : "failed");
        setLog((l) => [...l, `[${s}] exitCode=${exitCode}`]);
        if (onComplete) onComplete(exitCode, log.join("\n"));
      } catch {}
      es.close();
    });
    es.addEventListener("error", () => {
      setStatus("failed");
      es.close();
    });
  }

  return (
    <div className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Shot list">
          <select
            value={shotList}
            onChange={(e) => setShotList(e.target.value)}
            className="w-full px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
          >
            {availableShotLists.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Output filename">
          <input
            value={output}
            onChange={(e) => setOutput(e.target.value)}
            className="w-full px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm font-mono"
          />
        </Field>
        <Field label="Song (in raw/)">
          <select
            value={song}
            onChange={(e) => setSong(e.target.value)}
            className="w-full px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
          >
            <option value="">— none (silent track) —</option>
            {availableSongs.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Dialogue script JSON">
          <select
            value={dialogue}
            onChange={(e) => setDialogue(e.target.value)}
            className="w-full px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
          >
            <option value="">— none —</option>
            {availableDialogueScripts.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Color preset">
          <select
            value={colorPreset}
            onChange={(e) => setColorPreset(e.target.value)}
            disabled={!!colorPlan}
            className="w-full px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm disabled:opacity-50"
          >
            {COLOR_PRESETS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Color plan JSON (overrides preset)">
          <select
            value={colorPlan}
            onChange={(e) => setColorPlan(e.target.value)}
            className="w-full px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
          >
            <option value="">— none (use preset) —</option>
            {availableColorPlans.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Dimensions + FPS">
          <div className="flex gap-2">
            <input
              type="number"
              value={width}
              onChange={(e) => setWidth(Number(e.target.value))}
              className="w-20 px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
            />
            <span className="self-center">×</span>
            <input
              type="number"
              value={height}
              onChange={(e) => setHeight(Number(e.target.value))}
              className="w-20 px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
            />
            <span className="self-center">@</span>
            <input
              type="number"
              value={fps}
              onChange={(e) => setFps(Number(e.target.value))}
              className="w-16 px-2 py-1.5 bg-[var(--color-ink)] border border-white/10 rounded text-sm"
            />
            <span className="self-center text-xs">fps</span>
          </div>
        </Field>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={startRun}
          disabled={status === "running"}
          className="px-5 py-2 bg-[var(--color-forge)] text-[var(--color-ink)] rounded font-medium hover:bg-[var(--color-ember)] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {status === "running" ? "Running..." : "Run pipeline"}
        </button>
        {status !== "idle" && (
          <span
            className={`text-sm font-mono ${
              status === "running"
                ? "text-[var(--color-ember)]"
                : status === "completed"
                ? "text-green-400"
                : "text-red-400"
            }`}
          >
            {status}
            {runId && ` · ${runId}`}
          </span>
        )}
      </div>

      {log.length > 0 && (
        <div
          ref={logRef}
          className="border border-white/10 rounded bg-black/50 p-3 font-mono text-xs h-80 overflow-y-auto"
        >
          {log.map((line, i) => (
            <div
              key={i}
              className={
                line.startsWith("[ERROR]") || line.includes("✗") || line.includes("failed")
                  ? "text-red-400"
                  : line.includes("✓") || line.includes("completed")
                  ? "text-green-400"
                  : line.includes("warn")
                  ? "text-yellow-400"
                  : "text-[var(--color-mist)]"
              }
            >
              {line}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs uppercase tracking-wider text-[var(--color-ash)] mb-1">
        {label}
      </span>
      {children}
    </label>
  );
}
