"use client";

import { useMemo, useState } from "react";
import type { BeatMapData } from "@/lib/fs";

interface Props {
  data: BeatMapData;
  onSeek?: (timeSec: number) => void;
}

const TRACK_HEIGHT = 120;

export function BeatMapVisualizer({ data, onSeek }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const duration = data.duration_sec;

  const timeToX = (t: number) => (t / duration) * 100;

  const energyPath = useMemo(() => {
    if (!data.energy_curve || data.energy_curve.length === 0) return "";
    const points = data.energy_curve.map(([t, e], i) => {
      const x = timeToX(t);
      const y = TRACK_HEIGHT - e * TRACK_HEIGHT;
      return `${i === 0 ? "M" : "L"} ${x} ${y}`;
    });
    return points.join(" ");
  }, [data.energy_curve, duration]);

  const buildups = data.buildups ?? [];
  const breakdowns = data.breakdowns ?? [];
  const drops = data.drops ?? [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <Stat label="Song" value={data.song} />
        <Stat label="Artist" value={data.artist} />
        <Stat label="BPM" value={`${data.bpm}`} subtle={`confidence ${data.bpm_confidence}`} />
        <Stat
          label="Duration"
          value={`${Math.floor(data.duration_sec / 60)}:${String(Math.round(data.duration_sec % 60)).padStart(2, "0")}`}
          subtle={`${data.time_signature}`}
        />
      </div>

      <div className="border border-white/10 rounded p-4 bg-white/[0.02]">
        <div className="relative w-full" style={{ height: `${TRACK_HEIGHT}px` }}>
          <svg
            viewBox={`0 0 100 ${TRACK_HEIGHT}`}
            preserveAspectRatio="none"
            className={`absolute inset-0 w-full h-full ${onSeek ? "cursor-pointer" : ""}`}
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const x = ((e.clientX - rect.left) / rect.width) * duration;
              setHover(x);
            }}
            onMouseLeave={() => setHover(null)}
            onClick={(e) => {
              if (!onSeek) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const x = ((e.clientX - rect.left) / rect.width) * duration;
              onSeek(Math.max(0, Math.min(duration, x)));
            }}
          >
            {/* Buildups */}
            {buildups.map((b, i) => (
              <rect
                key={`buildup-${i}`}
                x={timeToX(b.start)}
                y={0}
                width={timeToX(b.end) - timeToX(b.start)}
                height={TRACK_HEIGHT}
                fill="#f5731b"
                fillOpacity={0.1}
              />
            ))}
            {/* Breakdowns */}
            {breakdowns.map((b, i) => (
              <rect
                key={`breakdown-${i}`}
                x={timeToX(b.start)}
                y={0}
                width={timeToX(b.end) - timeToX(b.start)}
                height={TRACK_HEIGHT}
                fill="#3e63dd"
                fillOpacity={0.1}
              />
            ))}
            {/* Energy curve */}
            {energyPath && (
              <path
                d={energyPath}
                stroke="#f6f5f1"
                strokeWidth={0.4}
                strokeOpacity={0.8}
                fill="none"
                vectorEffect="non-scaling-stroke"
              />
            )}
            {/* Downbeat markers */}
            {data.downbeats.map((t, i) => (
              <line
                key={`downbeat-${i}`}
                x1={timeToX(t)}
                x2={timeToX(t)}
                y1={TRACK_HEIGHT - 12}
                y2={TRACK_HEIGHT}
                stroke="#30a46c"
                strokeWidth={0.4}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {/* Drop markers */}
            {drops.map((d, i) => (
              <g key={`drop-${i}`}>
                <line
                  x1={timeToX(d.time)}
                  x2={timeToX(d.time)}
                  y1={0}
                  y2={TRACK_HEIGHT}
                  stroke="#e5484d"
                  strokeWidth={0.8}
                  strokeOpacity={0.8}
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  cx={timeToX(d.time)}
                  cy={8}
                  r={2}
                  fill="#e5484d"
                />
              </g>
            ))}
            {/* Hover indicator */}
            {hover !== null && (
              <line
                x1={timeToX(hover)}
                x2={timeToX(hover)}
                y1={0}
                y2={TRACK_HEIGHT}
                stroke="#ffab5c"
                strokeWidth={0.3}
                strokeOpacity={0.5}
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>
        </div>

        <div className="flex justify-between mt-2 text-xs text-[var(--color-ash)] font-mono">
          <span>0:00</span>
          <span>
            {hover !== null
              ? `${Math.floor(hover / 60)}:${String(Math.round(hover % 60)).padStart(2, "0")}`
              : ""}
          </span>
          <span>
            {Math.floor(duration / 60)}:{String(Math.round(duration % 60)).padStart(2, "0")}
          </span>
        </div>

        <div className="flex gap-4 mt-3 text-xs">
          <Legend color="#e5484d" label="drops" />
          <Legend color="#30a46c" label="downbeats" />
          <Legend color="#f5731b" label="buildups" />
          <Legend color="#3e63dd" label="breakdowns" />
          <Legend color="#f6f5f1" label="energy" />
        </div>
      </div>

      {drops.length > 0 && (
        <div>
          <div className="text-sm uppercase tracking-wider text-[var(--color-ash)] mb-2">
            Drops ({drops.length})
          </div>
          <div className="space-y-1 font-mono text-sm">
            {drops.map((d, i) => (
              <div key={i} className="flex gap-4 p-2 border border-white/5 rounded">
                <span className="text-[var(--color-forge)]">{d.type}</span>
                <span className="text-[var(--color-mist)]">
                  {Math.floor(d.time / 60)}:{String(Math.round(d.time % 60)).padStart(2, "0")}
                  .{String(Math.round((d.time % 1) * 100)).padStart(2, "0")}
                </span>
                <span className="text-[var(--color-ash)]">
                  intensity {d.intensity.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, subtle }: { label: string; value: string; subtle?: string }) {
  return (
    <div className="p-3 border border-white/5 rounded">
      <div className="text-xs uppercase tracking-wider text-[var(--color-ash)]">{label}</div>
      <div className="font-display text-lg truncate">{value}</div>
      {subtle && <div className="text-xs text-[var(--color-ash)] font-mono mt-0.5">{subtle}</div>}
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-[var(--color-ash)]">
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: color }}
      />
      {label}
    </span>
  );
}
