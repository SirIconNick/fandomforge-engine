"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BeatMapData } from "@/lib/fs";

interface Shot {
  id?: string;
  number?: number;
  song_time_sec?: number;
  start_frame?: number;
  duration_sec?: number;
  duration_frames?: number;
  source_id: string;
  source_timecode?: string;
  source_timestamp_sec?: number | null;
  description?: string;
  hero?: string;
  is_placeholder?: boolean;
}

export interface EditRoughPreviewProps {
  projectSlug: string;
  fps?: number;
  songPath?: string;
}

interface NormalizedShot {
  id: string;
  song_start_sec: number;
  duration_sec: number;
  source_id: string;
  source_start_sec: number;
  description: string;
  is_placeholder: boolean;
}

function timecodeToSec(tc: string | undefined): number {
  if (!tc) return 0;
  const parts = tc.split(":").map(Number);
  if (parts.length === 3) return (parts[0] ?? 0) * 3600 + (parts[1] ?? 0) * 60 + (parts[2] ?? 0);
  if (parts.length === 2) return (parts[0] ?? 0) * 60 + (parts[1] ?? 0);
  return Number(tc) || 0;
}

function normalizeShot(raw: Shot, fps: number): NormalizedShot {
  const songStart =
    raw.song_time_sec ?? (raw.start_frame != null ? raw.start_frame / fps : 0);
  const duration =
    raw.duration_sec ??
    (raw.duration_frames != null ? raw.duration_frames / fps : 2);
  const sourceStart =
    raw.source_timestamp_sec ?? timecodeToSec(raw.source_timecode);
  return {
    id: raw.id ?? `shot_${raw.number ?? Math.random().toString(36).slice(2, 8)}`,
    song_start_sec: Math.max(0, songStart),
    duration_sec: Math.max(0.1, duration),
    source_id: raw.source_id || "",
    source_start_sec: Math.max(0, sourceStart),
    description: raw.description ?? raw.hero ?? "",
    is_placeholder:
      raw.is_placeholder ||
      !raw.source_id ||
      raw.source_id.startsWith("PLACEHOLDER_"),
  };
}

export default function EditRoughPreview({
  projectSlug,
  fps = 24,
  songPath,
}: EditRoughPreviewProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [shots, setShots] = useState<NormalizedShot[]>([]);
  const [beatMap, setBeatMap] = useState<BeatMapData | null>(null);
  const [currentIdx, setCurrentIdx] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string>("");
  const [availableSources, setAvailableSources] = useState<Set<string>>(
    new Set()
  );
  const [songTime, setSongTime] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Fire all three fetches in parallel — none depend on each other.
      // Sequential awaits (the old pattern) cost ~3x the round-trip time on
      // a cold project load.
      const [shotsResult, beatResult, mediaResult] = await Promise.allSettled([
        fetch(`/api/project/${projectSlug}/shots`).then((r) => r.json()),
        fetch(
          `/api/artifacts/read?project=${encodeURIComponent(projectSlug)}&artifact=beat-map`
        ).then((r) => r.json()),
        fetch(`/api/project/${projectSlug}/media-list`).then(async (r) =>
          r.ok ? r.json() : null
        ),
      ]);
      if (cancelled) return;

      if (shotsResult.status === "fulfilled") {
        const shotsData = shotsResult.value;
        const raw: Shot[] = Array.isArray(shotsData?.shots) ? shotsData.shots : [];
        const normalized = raw.map((s) => normalizeShot(s, fps));
        normalized.sort((a, b) => a.song_start_sec - b.song_start_sec);
        setShots(normalized);
      } else {
        setError(`failed to load shots: ${shotsResult.reason}`);
      }

      if (beatResult.status === "fulfilled" && beatResult.value?.exists) {
        setBeatMap(beatResult.value.data as BeatMapData);
      }

      if (mediaResult.status === "fulfilled" && mediaResult.value) {
        const ids = new Set<string>(
          Array.isArray(mediaResult.value?.videos)
            ? (mediaResult.value.videos as { id: string }[]).map((v) => v.id)
            : []
        );
        setAvailableSources(ids);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectSlug, fps]);

  useEffect(() => {
    if (songPath) {
      setAudioUrl(
        `/api/project/${projectSlug}/audio/${encodeURIComponent(songPath)}`
      );
      return;
    }
    const commonNames = ["song.mp3", "song.wav", "song.m4a", "assets/song.mp3", "assets/song.wav"];
    (async () => {
      for (const name of commonNames) {
        const url = `/api/project/${projectSlug}/audio/${name}`;
        try {
          const head = await fetch(url, { method: "HEAD" });
          if (head.ok) {
            setAudioUrl(url);
            return;
          }
        } catch { /* try next */ }
      }
      setError((prev) => prev || "No song file found under assets/ — expected song.mp3/wav/m4a.");
    })();
  }, [projectSlug, songPath]);

  const shotAtTime = useCallback(
    (time: number): number => {
      for (let i = 0; i < shots.length; i++) {
        const s = shots[i]!;
        if (time >= s.song_start_sec && time < s.song_start_sec + s.duration_sec) {
          return i;
        }
      }
      return -1;
    },
    [shots]
  );

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTimeUpdate = () => {
      setSongTime(audio.currentTime);
      const idx = shotAtTime(audio.currentTime);
      if (idx !== currentIdx) {
        setCurrentIdx(idx);
      }
    };
    audio.addEventListener("timeupdate", onTimeUpdate);
    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
    };
  }, [shotAtTime, currentIdx]);

  useEffect(() => {
    const video = videoRef.current;
    const shot = currentIdx >= 0 ? shots[currentIdx] : null;
    if (!video || !shot) return;

    if (shot.is_placeholder || !availableSources.has(shot.source_id)) {
      return;
    }

    const expectedSrc = `/api/project/${projectSlug}/video/${encodeURIComponent(shot.source_id)}.mp4`;
    if (!video.src.endsWith(expectedSrc.split("/").pop() ?? "")) {
      video.src = expectedSrc;
      video.onloadedmetadata = () => {
        video.currentTime = shot.source_start_sec;
        if (playing) void video.play().catch(() => {});
      };
    } else {
      video.currentTime = shot.source_start_sec;
      if (playing) void video.play().catch(() => {});
    }
  }, [currentIdx, shots, projectSlug, playing, availableSources]);

  function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
      videoRef.current?.pause();
      setPlaying(false);
    } else {
      void audio.play();
      setPlaying(true);
    }
  }

  function seek(sec: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration || sec, sec));
  }

  const totalDuration = useMemo(() => {
    if (beatMap?.duration_sec) return beatMap.duration_sec;
    const last = shots[shots.length - 1];
    return last ? last.song_start_sec + last.duration_sec : 60;
  }, [beatMap, shots]);

  const currentShot = currentIdx >= 0 ? shots[currentIdx] : null;
  const currentPlaceholder =
    !!currentShot &&
    (currentShot.is_placeholder || !availableSources.has(currentShot.source_id));

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-xs text-yellow-300 bg-yellow-500/10 border border-yellow-500/30 rounded p-2">
          {error}
        </div>
      )}

      <div className="relative aspect-video bg-black rounded overflow-hidden border border-white/10">
        <video
          ref={videoRef}
          className="w-full h-full object-contain"
          muted
          playsInline
        />
        {currentPlaceholder && currentShot && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-6 bg-black/70">
            <div className="text-[10px] uppercase tracking-wide text-white/50 mb-1">
              placeholder shot
            </div>
            <div className="text-2xl font-display text-[var(--color-forge,#ff5a1f)] mb-2">
              {currentShot.source_id || "—"}
            </div>
            <div className="text-sm text-white/70 max-w-md">
              {currentShot.description || "(no description)"}
            </div>
            <div className="text-[10px] text-white/40 mt-3 font-mono">
              @{currentShot.song_start_sec.toFixed(2)}s · {currentShot.duration_sec.toFixed(1)}s
            </div>
          </div>
        )}
        {!currentShot && shots.length > 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-white/40 text-sm">
            Press play to preview the edit
          </div>
        )}
        {shots.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-white/40 text-sm">
            No shots in this project yet — draft a shot list first.
          </div>
        )}
      </div>

      {audioUrl && (
        <audio ref={audioRef} src={audioUrl} preload="metadata" className="hidden" />
      )}

      <div className="space-y-2">
        <div className="relative h-8 bg-black/40 border border-white/10 rounded overflow-hidden">
          {shots.map((s, i) => {
            const leftPct = (s.song_start_sec / totalDuration) * 100;
            const widthPct = (s.duration_sec / totalDuration) * 100;
            return (
              <button
                key={s.id}
                onClick={() => seek(s.song_start_sec)}
                className={`absolute top-0 bottom-0 border-r border-white/10 transition-opacity hover:opacity-100 ${
                  i === currentIdx
                    ? "bg-[var(--color-forge,#ff5a1f)]/60 opacity-100"
                    : s.is_placeholder
                      ? "bg-white/5 opacity-60"
                      : "bg-blue-500/25 opacity-70"
                }`}
                style={{ left: `${leftPct}%`, width: `${Math.max(0.3, widthPct)}%` }}
                title={`#${i + 1} · ${s.description || s.source_id}`}
              />
            );
          })}
          {beatMap?.drops?.map((d, i) => (
            <div
              key={`drop_${i}`}
              className="absolute top-0 bottom-0 w-0.5 bg-red-400/80"
              style={{ left: `${(d.time / totalDuration) * 100}%` }}
              title={`drop @ ${d.time.toFixed(1)}s`}
            />
          ))}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white pointer-events-none"
            style={{ left: `${(songTime / totalDuration) * 100}%` }}
          />
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={togglePlay}
            disabled={!audioUrl || shots.length === 0}
            className="px-3 py-1.5 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-xs disabled:opacity-40"
          >
            {playing ? "Pause" : "Play"}
          </button>
          <button
            onClick={() => seek(0)}
            className="px-3 py-1.5 rounded border border-white/20 text-xs"
          >
            ⏮ Start
          </button>
          <span className="text-xs text-white/60 font-mono ml-auto">
            {songTime.toFixed(2)}s / {totalDuration.toFixed(1)}s · shot{" "}
            {currentIdx >= 0 ? currentIdx + 1 : "–"} / {shots.length}
          </span>
        </div>

        <div className="text-[10px] text-white/40">
          {availableSources.size > 0
            ? `${availableSources.size} source videos available in raw/`
            : "No source videos ingested yet — everything renders as placeholder"}
          · {shots.filter((s) => s.is_placeholder).length} placeholder shots
          · {beatMap?.drops?.length ?? 0} drops marked
        </div>
      </div>
    </div>
  );
}
