"use client";

import { useEffect, useState } from "react";
import { Timeline, type ShotRow } from "@/components/Timeline";
import ShotListEditor from "@/components/ShotListEditor";

interface Props {
  project: string;
  shotLists: string[];
}

type Mode = "view" | "edit";

export function EditorShell({ project, shotLists }: Props) {
  const [mode, setMode] = useState<Mode>("view");
  const [selectedList, setSelectedList] = useState(shotLists[0] ?? "shot-list.md");
  const [shots, setShots] = useState<ShotRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedList) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/project/${project}/shots?file=${encodeURIComponent(selectedList)}`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) setError(data.error);
        else setShots(Array.isArray(data?.shots) ? data.shots : []);
      })
      .catch((err) => !cancelled && setError(String(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [project, selectedList]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex border border-white/10 rounded">
          <button
            onClick={() => setMode("view")}
            className={`px-3 py-1 text-sm ${
              mode === "view" ? "bg-white/10" : ""
            }`}
          >
            View (markdown)
          </button>
          <button
            onClick={() => setMode("edit")}
            className={`px-3 py-1 text-sm border-l border-white/10 ${
              mode === "edit" ? "bg-white/10" : ""
            }`}
          >
            Edit (JSON)
          </button>
        </div>
        {mode === "view" && shotLists.length > 1 && (
          <div className="flex flex-wrap gap-2">
            {shotLists.map((s) => (
              <button
                key={s}
                onClick={() => setSelectedList(s)}
                className={`px-3 py-1.5 rounded text-sm font-mono ${
                  s === selectedList
                    ? "bg-[var(--color-forge)] text-[var(--color-ink)]"
                    : "border border-white/10 hover:bg-white/5"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      {mode === "edit" && <ShotListEditor slug={project} />}

      {mode === "view" && loading && (
        <div className="text-[var(--color-ash)] text-sm">Loading shots...</div>
      )}

      {mode === "view" && error && (
        <div className="p-4 border border-red-400/30 bg-red-400/5 rounded text-sm text-red-400">
          {error}
        </div>
      )}

      {mode === "view" && !loading && !error && shots.length > 0 && (
        <Timeline project={project} shots={shots} />
      )}

      {mode === "view" && !loading && !error && shots.length === 0 && (
        <div className="p-10 border border-dashed border-white/10 rounded text-center text-[var(--color-mist)]">
          No shots parsed from{" "}
          <code className="text-[var(--color-ember)]">{selectedList}</code>. Check
          the file exists and has a valid table format.
        </div>
      )}
    </div>
  );
}
