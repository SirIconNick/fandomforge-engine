"use client";

import { useEffect, useState } from "react";
import { VideoPlayer, type ShotMarker } from "@/components/VideoPlayer";
import { BeatMapVisualizer } from "@/components/BeatMapVisualizer";
import type { BeatMapData } from "@/lib/fs";

interface Props {
  project: string;
  videos: string[];
}

interface Shot {
  number: number;
  song_time_sec: number;
  duration_sec: number;
  hero: string;
  description: string;
  is_placeholder: boolean;
}

interface DialogueCue {
  audio: string;
  start: number;
  duration: number;
  character: string;
  line?: string;
}

export function PlayerShell({ project, videos }: Props) {
  const [selected, setSelected] = useState(videos[0]);
  const [shots, setShots] = useState<ShotMarker[]>([]);
  const [cues, setCues] = useState<DialogueCue[]>([]);
  const [beatMap, setBeatMap] = useState<BeatMapData | null>(null);
  const [seekRequest, setSeekRequest] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/project/${project}/shots`);
        const data = await res.json();
        if (cancelled) return;
        if (Array.isArray(data?.shots)) {
          const markers: ShotMarker[] = data.shots.map((s: Shot) => ({
            index: s.number,
            start: s.song_time_sec,
            duration: s.duration_sec,
            label: s.description?.slice(0, 40),
            hero: s.hero,
          }));
          setShots(markers);
        }
      } catch {}
      try {
        const res = await fetch(`/api/project/${project}/dialogue`);
        const data = await res.json();
        if (cancelled) return;
        if (Array.isArray(data?.cues)) setCues(data.cues);
      } catch {}
      try {
        const res = await fetch(`/api/beat-map/${project}`);
        if (res.ok) {
          const data = (await res.json()) as BeatMapData;
          if (!cancelled) setBeatMap(data);
        }
      } catch {}
    })();
    return () => {
      cancelled = true;
    };
  }, [project]);

  const videoUrl = `/api/project/${project}/video/exports/${encodeURIComponent(selected)}`;

  return (
    <div className="space-y-4">
      {videos.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {videos.map((v) => (
            <button
              key={v}
              onClick={() => setSelected(v)}
              className={`px-3 py-1.5 rounded text-sm font-mono ${
                v === selected
                  ? "bg-[var(--color-forge)] text-[var(--color-ink)]"
                  : "border border-white/10 hover:bg-white/5"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      )}

      <VideoPlayer
        src={videoUrl}
        shots={shots}
        dialogueCues={cues}
        seekRequest={seekRequest}
      />

      {beatMap && (
        <div className="space-y-2">
          <p className="text-xs text-[var(--color-ash)]">
            Click the waveform to jump the player to that moment.
          </p>
          <BeatMapVisualizer data={beatMap} onSeek={(t) => setSeekRequest(t)} />
        </div>
      )}

      {shots.length > 0 && (
        <div className="text-xs text-[var(--color-ash)]">
          Showing {shots.length} shot markers from shot-list.md. Dialogue cues:{" "}
          {cues.length}.
        </div>
      )}
    </div>
  );
}
