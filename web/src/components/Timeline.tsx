"use client";

import { useMemo, useState, useEffect, useRef } from "react";

export interface ShotRow {
  number: number;
  song_time_sec: number;
  duration_sec: number;
  source_id: string;
  source_timestamp: string;
  source_timestamp_sec?: number | null;
  hero: string;
  description: string;
  mood: string;
  act: number;
  is_placeholder: boolean;
}

interface Props {
  project: string;
  shots: ShotRow[];
  onClipSelect?: (shot: ShotRow) => void;
}

const ACT_COLORS = [
  "rgba(245, 115, 27, 0.2)",
  "rgba(62, 99, 221, 0.2)",
  "rgba(18, 165, 148, 0.2)",
  "rgba(229, 72, 77, 0.2)",
  "rgba(142, 78, 198, 0.2)",
];

function ShotThumb({ url }: { url: string }) {
  const [ok, setOk] = useState(true);
  if (!ok) return null;
  return (
    <img
      src={url}
      alt=""
      className="w-full aspect-video object-cover bg-black/40"
      loading="lazy"
      onError={() => setOk(false)}
    />
  );
}

export function Timeline({ project, shots, onClipSelect }: Props) {
  const [selectedShot, setSelectedShot] = useState<ShotRow | null>(null);
  const [undoStatus, setUndoStatus] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const totalDuration = useMemo(
    () => shots.reduce((sum, s) => sum + s.duration_sec, 0),
    [shots],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLInputElement
      )
        return;
      if (shots.length === 0) return;
      const currentIdx = selectedShot
        ? shots.findIndex((s) => s.number === selectedShot.number)
        : -1;
      const next = (idx: number) => {
        const bounded = Math.max(0, Math.min(shots.length - 1, idx));
        const target = shots[bounded];
        if (target) {
          setSelectedShot(target);
          onClipSelect?.(target);
        }
      };
      if (e.key === "ArrowLeft" || e.key === "j" || e.key === "J") {
        e.preventDefault();
        next(currentIdx < 0 ? 0 : currentIdx - 1);
      } else if (e.key === "ArrowRight" || e.key === "l" || e.key === "L") {
        e.preventDefault();
        next(currentIdx < 0 ? 0 : currentIdx + 1);
      } else if (e.key === "Home") {
        e.preventDefault();
        next(0);
      } else if (e.key === "End") {
        e.preventDefault();
        next(shots.length - 1);
      } else if (e.key === "Escape") {
        setSelectedShot(null);
      } else if ((e.metaKey || e.ctrlKey) && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        void rollbackShotList();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shots, selectedShot, onClipSelect]);

  async function rollbackShotList() {
    setUndoStatus("rolling back…");
    try {
      const res = await fetch("/api/artifacts/rollback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_slug: project,
          artifact: "shot-list",
          steps: 1,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setUndoStatus(body.error ?? `rollback failed (${res.status})`);
      } else {
        setUndoStatus("rolled back — refresh to see changes");
      }
    } catch (err) {
      setUndoStatus((err as Error).message);
    }
    setTimeout(() => setUndoStatus(null), 4000);
  }

  const shotsByAct = useMemo(() => {
    const map = new Map<number, ShotRow[]>();
    shots.forEach((s) => {
      if (!map.has(s.act)) map.set(s.act, []);
      map.get(s.act)!.push(s);
    });
    return Array.from(map.entries()).sort((a, b) => a[0] - b[0]);
  }, [shots]);

  return (
    <div className="space-y-4" ref={containerRef}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-sm text-[var(--color-ash)]">
          {shots.length} shots · {totalDuration.toFixed(1)}s total runtime ·{" "}
          {shots.filter((s) => s.is_placeholder).length} placeholders
        </div>
        <div className="text-[10px] text-white/40 font-mono">
          shortcuts: ←/→ or J/L prev/next · home/end jump · esc clear · ⌘Z undo shot-list
        </div>
      </div>
      {undoStatus && (
        <div className="text-xs text-[var(--color-forge)] bg-[var(--color-forge)]/10 border border-[var(--color-forge)]/30 rounded p-2">
          {undoStatus}
        </div>
      )}

      {/* Horizontal ribbon — full timeline */}
      <div className="relative h-16 rounded bg-[var(--color-ink)] border border-white/10 overflow-hidden">
        <div className="flex h-full">
          {shots.map((s, i) => {
            const widthPct = (s.duration_sec / Math.max(totalDuration, 1)) * 100;
            const selected = selectedShot?.number === s.number;
            return (
              <button
                key={s.number}
                onClick={() => {
                  setSelectedShot(s);
                  onClipSelect?.(s);
                }}
                className={`group relative border-r border-white/5 flex items-center justify-center transition-colors hover:brightness-125 ${
                  selected ? "ring-2 ring-[var(--color-forge)] ring-inset z-10" : ""
                }`}
                style={{
                  width: `${widthPct}%`,
                  minWidth: "4px",
                  background: s.is_placeholder
                    ? "rgba(40,40,40,0.8)"
                    : ACT_COLORS[(s.act - 1) % ACT_COLORS.length],
                }}
                title={`#${s.number} · ${s.hero || s.description.slice(0, 40)}`}
              >
                {widthPct > 2.5 && (
                  <span className="text-[10px] font-mono text-[var(--color-mist)] opacity-70 group-hover:opacity-100">
                    {s.number}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Acts grouped as cards */}
      <div className="space-y-6">
        {shotsByAct.map(([actNum, actShots]) => (
          <div key={actNum}>
            <div className="text-xs uppercase tracking-wider text-[var(--color-ash)] mb-2">
              Act {actNum} · {actShots.length} shots ·{" "}
              {actShots.reduce((sum, s) => sum + s.duration_sec, 0).toFixed(1)}s
            </div>
            <div className="grid sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
              {actShots.map((s) => {
                const selected = selectedShot?.number === s.number;
                const thumbUrl =
                  s.source_id && !s.is_placeholder
                    ? `/api/project/${encodeURIComponent(project)}/thumb?source=${encodeURIComponent(s.source_id)}&time=${s.source_timestamp_sec ?? 0}`
                    : null;
                return (
                  <button
                    key={s.number}
                    onClick={() => {
                      setSelectedShot(s);
                      onClipSelect?.(s);
                    }}
                    className={`rounded border text-left transition-colors overflow-hidden ${
                      selected
                        ? "border-[var(--color-forge)] bg-[var(--color-forge)]/10"
                        : "border-white/10 hover:border-white/30"
                    }`}
                  >
                    {thumbUrl && <ShotThumb url={thumbUrl} />}
                    <div className="p-3">
                      <div className="flex items-baseline justify-between mb-1">
                        <span className="font-mono text-xs text-[var(--color-ash)]">
                          #{s.number}
                        </span>
                        <span className="font-mono text-xs">
                          {s.duration_sec}s
                        </span>
                      </div>
                      {s.hero && (
                        <div className="text-xs font-medium mb-1 truncate">
                          {s.hero}
                        </div>
                      )}
                      <div className="text-xs text-[var(--color-mist)] line-clamp-2">
                        {s.description || "(no description)"}
                      </div>
                      <div className="text-[10px] font-mono text-[var(--color-ash)] mt-1 truncate">
                        {s.source_id || "—"}
                        {s.source_timestamp && ` @ ${s.source_timestamp}`}
                      </div>
                      {s.mood && (
                        <div className="text-[10px] text-[var(--color-ember)] mt-1">
                          {s.mood}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {selectedShot && (
        <div className="border border-[var(--color-forge)]/50 rounded p-4 bg-[var(--color-forge)]/5">
          <div className="flex items-baseline justify-between mb-3">
            <div>
              <h3 className="font-display text-xl">
                Shot #{selectedShot.number}
                {selectedShot.hero && ` · ${selectedShot.hero}`}
              </h3>
              <div className="text-xs text-[var(--color-ash)] font-mono">
                song_time={selectedShot.song_time_sec}s · duration=
                {selectedShot.duration_sec}s · act={selectedShot.act}
              </div>
            </div>
            <button
              onClick={() => setSelectedShot(null)}
              className="text-xs text-[var(--color-ash)] hover:text-white"
            >
              close
            </button>
          </div>
          <div className="grid sm:grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-ash)] mb-1">
                Description
              </div>
              <div>{selectedShot.description || "—"}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-ash)] mb-1">
                Source + Timestamp
              </div>
              <div className="font-mono text-xs">
                {selectedShot.source_id || "—"}
                {selectedShot.source_timestamp && (
                  <> @ {selectedShot.source_timestamp}</>
                )}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-ash)] mb-1">
                Mood
              </div>
              <div>{selectedShot.mood || "—"}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-ash)] mb-1">
                Status
              </div>
              <div>
                {selectedShot.is_placeholder ? (
                  <span className="text-[var(--color-ash)]">
                    placeholder (renders as black)
                  </span>
                ) : (
                  <span className="text-[var(--color-ember)]">
                    live clip from {selectedShot.source_id}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
