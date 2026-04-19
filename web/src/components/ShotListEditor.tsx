"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface Shot {
  id: string;
  act: number;
  start_frame: number;
  duration_frames: number;
  source_id: string;
  source_timecode: string;
  role: string;
  mood_tags?: string[];
  description?: string;
  fandom?: string;
  cliche_flag?: boolean;
  override_reason?: string;
  safe_area_ok?: boolean;
  reuse_index?: number;
  characters?: string[];
  scores?: {
    theme_fit?: number;
    fandom_balance?: number;
    emotion?: number;
    beat_sync_score?: number;
  };
  beat_sync?: {
    type: string;
    index: number;
    time_sec?: number;
  };
  framing?: string;
  motion_vector?: number | null;
  eyeline?: string;
  transition_to_next?: string;
}

export interface ShotList {
  schema_version: number;
  project_slug: string;
  fps: number;
  resolution: { width: number; height: number };
  song_duration_sec?: number;
  fandom_quota?: Record<string, Record<string, number>>;
  shots: Shot[];
  rejected?: unknown[];
  generated_at?: string;
  generator?: string;
}

const ROLES = [
  "hero",
  "action",
  "reaction",
  "environment",
  "detail",
  "motion",
  "gaze",
  "cut-on-action",
  "establishing",
  "transition",
  "insert",
  "title",
];
const TRANSITIONS = [
  "hard_cut",
  "match_cut",
  "flash_cut",
  "whip_pan",
  "dip_to_black",
  "cross_dissolve",
  "speed_ramp",
  "flash_stack",
  "morph",
  "none",
];

export default function ShotListEditor({ slug }: { slug: string }) {
  const [list, setList] = useState<ShotList | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const dragIdRef = useRef<string | null>(null);

  useEffect(() => {
    fetch(`/api/project/${slug}/shot-list`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return (await r.json()) as ShotList;
      })
      .then(setList)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [slug]);

  const totalFrames = useMemo(
    () => (list?.shots ?? []).reduce((s, x) => s + x.duration_frames, 0),
    [list]
  );

  function recomputeStartFrames(shots: Shot[]): Shot[] {
    let cursor = 0;
    return shots.map((s) => {
      const next: Shot = { ...s, start_frame: cursor };
      cursor += s.duration_frames;
      return next;
    });
  }

  function setShots(next: Shot[]) {
    if (!list) return;
    setList({ ...list, shots: recomputeStartFrames(next) });
    setDirty(true);
  }

  function updateShot(id: string, patch: Partial<Shot>) {
    if (!list) return;
    const next = list.shots.map((s) => (s.id === id ? { ...s, ...patch } : s));
    setShots(next);
  }

  function deleteShot(id: string) {
    if (!list) return;
    if (!confirm(`Delete shot ${id}? This cannot be undone until you reload.`)) return;
    const next = list.shots.filter((s) => s.id !== id);
    setShots(next);
    if (selectedId === id) setSelectedId(null);
  }

  function duplicateShot(id: string) {
    if (!list) return;
    const idx = list.shots.findIndex((s) => s.id === id);
    if (idx < 0) return;
    const source = list.shots[idx];
    const copy: Shot = {
      ...source,
      id: `${source.id}-copy-${Date.now().toString(36)}`,
      reuse_index: (source.reuse_index ?? 0) + 1,
    };
    const next = [...list.shots.slice(0, idx + 1), copy, ...list.shots.slice(idx + 1)];
    setShots(next);
    setSelectedId(copy.id);
  }

  function moveShot(id: string, delta: number) {
    if (!list) return;
    const idx = list.shots.findIndex((s) => s.id === id);
    if (idx < 0) return;
    const next = [...list.shots];
    const [item] = next.splice(idx, 1);
    const newIdx = Math.max(0, Math.min(next.length, idx + delta));
    next.splice(newIdx, 0, item);
    setShots(next);
  }

  function onDragStart(id: string) {
    dragIdRef.current = id;
  }

  function onDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
  }

  function onDrop(targetId: string) {
    const fromId = dragIdRef.current;
    dragIdRef.current = null;
    if (!list || !fromId || fromId === targetId) return;
    const next = [...list.shots];
    const fromIdx = next.findIndex((s) => s.id === fromId);
    const toIdx = next.findIndex((s) => s.id === targetId);
    if (fromIdx < 0 || toIdx < 0) return;
    const [item] = next.splice(fromIdx, 1);
    next.splice(toIdx, 0, item);
    setShots(next);
  }

  async function save() {
    if (!list) return;
    setSaving(true);
    setError("");
    setStatus("saving…");
    try {
      const res = await fetch(`/api/project/${slug}/shot-list`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(list),
      });
      const body = (await res.json()) as { ok?: boolean; error?: string; stdout?: string };
      if (!res.ok || !body.ok) {
        setError(body.error ?? `${res.status} ${res.statusText}`);
        setStatus("");
        return;
      }
      setDirty(false);
      setStatus("saved");
      setTimeout(() => setStatus(""), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("");
    } finally {
      setSaving(false);
    }
  }

  if (error && !list) {
    return (
      <div className="text-sm text-red-300 border border-red-500/30 rounded p-3">
        Failed to load shot list: {error}
      </div>
    );
  }
  if (!list) {
    return <div className="text-sm text-white/50">Loading shot-list.json…</div>;
  }

  const fps = list.fps;
  const selected = list.shots.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="space-y-3">
      <header className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-sm text-white/60">
          {list.shots.length} shots · {(totalFrames / fps).toFixed(2)}s runtime · fps {fps}
        </div>
        <div className="flex items-center gap-2">
          {dirty && <span className="text-xs text-yellow-400">unsaved edits</span>}
          {status && <span className="text-xs text-white/60">{status}</span>}
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="px-3 py-1 rounded bg-[var(--color-forge,#ff5a1f)] text-black text-sm font-semibold disabled:opacity-50"
          >
            {saving ? "saving…" : "Save"}
          </button>
        </div>
      </header>

      {error && (
        <div className="text-xs text-red-300 border border-red-500/30 rounded p-2 bg-red-500/10">
          {error}
        </div>
      )}

      <div className="grid lg:grid-cols-[1fr_360px] gap-3">
        <div className="space-y-1">
          {list.shots.map((s, idx) => (
            <div
              key={s.id}
              draggable
              onDragStart={() => onDragStart(s.id)}
              onDragOver={onDragOver}
              onDrop={() => onDrop(s.id)}
              onClick={() => setSelectedId(s.id)}
              className={`flex items-center gap-3 border rounded px-3 py-2 text-xs cursor-grab active:cursor-grabbing ${
                selectedId === s.id
                  ? "border-[var(--color-forge,#ff5a1f)] bg-white/5"
                  : "border-white/10 hover:border-white/20"
              } ${s.cliche_flag ? "border-l-4 border-l-red-500" : ""}`}
            >
              <span className="text-white/40 w-8 text-right font-mono">{idx + 1}</span>
              <span className="text-white/70 min-w-12 font-mono">act {s.act}</span>
              <span className="text-white/90 truncate flex-1">
                {s.description || `${s.source_id} @ ${s.source_timecode}`}
              </span>
              <span className="text-white/50 font-mono w-16 text-right">
                {(s.duration_frames / fps).toFixed(2)}s
              </span>
              <span className="text-white/40 w-16 text-right">{s.role}</span>
              <div className="flex gap-1">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    moveShot(s.id, -1);
                  }}
                  className="px-1.5 rounded hover:bg-white/5"
                  title="move up"
                >
                  ↑
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    moveShot(s.id, 1);
                  }}
                  className="px-1.5 rounded hover:bg-white/5"
                  title="move down"
                >
                  ↓
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    duplicateShot(s.id);
                  }}
                  className="px-1.5 rounded hover:bg-white/5"
                  title="duplicate"
                >
                  ⧉
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteShot(s.id);
                  }}
                  className="px-1.5 rounded hover:bg-red-500/20 text-red-300"
                  title="delete"
                >
                  ×
                </button>
              </div>
            </div>
          ))}
          {list.shots.length === 0 && (
            <div className="text-white/50 text-sm border border-dashed border-white/10 rounded p-6 text-center">
              Shot list is empty. Run <code className="font-mono bg-white/5 px-1 rounded">ff match shots</code>
              {" "}
              to populate it.
            </div>
          )}
        </div>

        <aside className="border border-white/10 rounded p-3 bg-white/[0.02] space-y-3 text-sm h-max sticky top-4">
          {selected ? (
            <ShotForm
              shot={selected}
              fps={fps}
              onChange={(patch) => updateShot(selected.id, patch)}
            />
          ) : (
            <div className="text-white/50 text-xs">Select a shot to edit.</div>
          )}
        </aside>
      </div>
    </div>
  );
}

function ShotForm({
  shot,
  fps,
  onChange,
}: {
  shot: Shot;
  fps: number;
  onChange: (patch: Partial<Shot>) => void;
}) {
  return (
    <div className="space-y-3">
      <div>
        <div className="text-[10px] uppercase tracking-wide text-white/50 mb-1">id</div>
        <div className="font-mono text-xs text-white/80 break-all">{shot.id}</div>
      </div>
      <Field label="description">
        <textarea
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm min-h-14"
          value={shot.description ?? ""}
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label={`duration (${(shot.duration_frames / fps).toFixed(2)}s)`}>
          <input
            type="number"
            min={1}
            className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
            value={shot.duration_frames}
            onChange={(e) =>
              onChange({ duration_frames: Math.max(1, parseInt(e.target.value, 10) || 1) })
            }
          />
        </Field>
        <Field label="act">
          <input
            type="number"
            min={1}
            max={5}
            className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
            value={shot.act}
            onChange={(e) =>
              onChange({ act: Math.max(1, Math.min(5, parseInt(e.target.value, 10) || 1)) })
            }
          />
        </Field>
      </div>
      <Field label="role">
        <select
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
          value={shot.role}
          onChange={(e) => onChange({ role: e.target.value })}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>
      <Field label="fandom">
        <input
          type="text"
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
          value={shot.fandom ?? ""}
          onChange={(e) => onChange({ fandom: e.target.value })}
        />
      </Field>
      <Field label="mood tags (comma)">
        <input
          type="text"
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
          value={(shot.mood_tags ?? []).join(", ")}
          onChange={(e) =>
            onChange({
              mood_tags: e.target.value
                .split(",")
                .map((m) => m.trim())
                .filter(Boolean),
            })
          }
        />
      </Field>
      <Field label="source">
        <div className="grid grid-cols-[1fr_110px] gap-1">
          <input
            type="text"
            className="bg-black/30 border border-white/10 rounded px-2 py-1 text-sm font-mono"
            value={shot.source_id}
            onChange={(e) => onChange({ source_id: e.target.value })}
            placeholder="source_id"
          />
          <input
            type="text"
            className="bg-black/30 border border-white/10 rounded px-2 py-1 text-sm font-mono"
            value={shot.source_timecode}
            onChange={(e) => onChange({ source_timecode: e.target.value })}
            placeholder="HH:MM:SS"
          />
        </div>
      </Field>
      <Field label="transition to next">
        <select
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm"
          value={shot.transition_to_next ?? "hard_cut"}
          onChange={(e) => onChange({ transition_to_next: e.target.value })}
        >
          {TRANSITIONS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </Field>
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={shot.cliche_flag ?? false}
          onChange={(e) => onChange({ cliche_flag: e.target.checked })}
        />
        cliche_flag
      </label>
      {shot.cliche_flag && (
        <Field label="override_reason (required to pass QA)">
          <textarea
            className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-sm min-h-14"
            value={shot.override_reason ?? ""}
            onChange={(e) => onChange({ override_reason: e.target.value })}
          />
        </Field>
      )}
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={shot.safe_area_ok ?? true}
          onChange={(e) => onChange({ safe_area_ok: e.target.checked })}
        />
        safe_area_ok
      </label>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-[10px] uppercase tracking-wide text-white/50 mb-1">{label}</div>
      {children}
    </label>
  );
}
