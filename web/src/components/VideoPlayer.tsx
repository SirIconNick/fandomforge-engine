"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface ShotMarker {
  index: number;
  start: number;
  duration: number;
  label?: string;
  hero?: string;
}

interface Props {
  src: string;
  shots?: ShotMarker[];
  dialogueCues?: { start: number; duration: number; character: string; line?: string }[];
  fps?: number;
  autoplay?: boolean;
  /**
   * Imperative seek. When this number changes (in seconds), the player seeks
   * to that time. Null disables. Pair with BeatMapVisualizer's onSeek so
   * clicking the waveform jumps the player.
   */
  seekRequest?: number | null;
}

const FRAME_STEP_RATIOS = [-1, -0.25, 0, 0.25, 1] as const;

export function VideoPlayer({
  src,
  shots = [],
  dialogueCues = [],
  fps = 24,
  autoplay = false,
  seekRequest,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [looping, setLooping] = useState(false);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);

  const frameDuration = 1 / fps;

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const onTime = () => setCurrentTime(el.currentTime);
    const onLoad = () => setDuration(el.duration || 0);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("loadedmetadata", onLoad);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("loadedmetadata", onLoad);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
    };
  }, []);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.playbackRate = playbackRate;
  }, [playbackRate]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.volume = volume;
    el.muted = muted;
  }, [volume, muted]);

  // Imperative seek — parent changes seekRequest to any new number and the
  // player jumps there.
  useEffect(() => {
    if (seekRequest == null) return;
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, Math.min(duration || seekRequest, seekRequest));
  }, [seekRequest, duration]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      const el = videoRef.current;
      if (!el) return;
      switch (e.key) {
        case " ":
          e.preventDefault();
          el.paused ? el.play() : el.pause();
          break;
        case "j":
          el.currentTime = Math.max(0, el.currentTime - 5);
          break;
        case "k":
          el.paused ? el.play() : el.pause();
          break;
        case "l":
          el.currentTime = Math.min(duration, el.currentTime + 5);
          break;
        case "ArrowLeft":
          e.preventDefault();
          el.currentTime = Math.max(0, el.currentTime - (e.shiftKey ? 10 : 1));
          break;
        case "ArrowRight":
          e.preventDefault();
          el.currentTime = Math.min(
            duration,
            el.currentTime + (e.shiftKey ? 10 : 1),
          );
          break;
        case ",":
          e.preventDefault();
          el.currentTime = Math.max(0, el.currentTime - frameDuration);
          break;
        case ".":
          e.preventDefault();
          el.currentTime = Math.min(duration, el.currentTime + frameDuration);
          break;
        case "m":
          setMuted((m) => !m);
          break;
        case "0":
          el.currentTime = 0;
          break;
        case "L":
          setLooping((x) => !x);
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [duration, frameDuration]);

  const handleLoopingEnded = () => {
    const el = videoRef.current;
    if (!el) return;
    if (looping) {
      el.currentTime = 0;
      el.play();
    }
  };

  const seek = (time: number) => {
    const el = videoRef.current;
    if (el) el.currentTime = Math.max(0, Math.min(duration, time));
  };

  const currentShot = useMemo(() => {
    return shots.find(
      (s) => currentTime >= s.start && currentTime < s.start + s.duration,
    );
  }, [shots, currentTime]);

  const progressPct = duration ? (currentTime / duration) * 100 : 0;

  return (
    <div className="rounded border border-white/10 overflow-hidden bg-black">
      <div className="relative aspect-video bg-black">
        <video
          ref={videoRef}
          src={src}
          autoPlay={autoplay}
          onEnded={handleLoopingEnded}
          className="w-full h-full"
          playsInline
        />
        {currentShot && (
          <div className="absolute top-3 left-3 px-2 py-1 rounded bg-black/70 text-xs font-mono text-[var(--color-ember)]">
            shot #{currentShot.index}
            {currentShot.hero && ` · ${currentShot.hero}`}
            {currentShot.label && ` · ${currentShot.label}`}
          </div>
        )}
        <div className="absolute top-3 right-3 px-2 py-1 rounded bg-black/70 text-xs font-mono text-[var(--color-mist)]">
          {formatTime(currentTime)} / {formatTime(duration)}
        </div>
      </div>

      {/* Timeline */}
      <div
        className="relative h-14 bg-[var(--color-ink)] cursor-pointer"
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const pct = (e.clientX - rect.left) / rect.width;
          seek(pct * duration);
        }}
      >
        {/* Shot blocks */}
        {duration > 0 && shots.length > 0 && (
          <div className="absolute inset-0">
            {shots.map((s) => {
              const left = (s.start / duration) * 100;
              const width = (s.duration / duration) * 100;
              return (
                <div
                  key={s.index}
                  className="absolute top-0 bottom-2 border-l border-white/10"
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={`shot ${s.index}${s.label ? " · " + s.label : ""}`}
                >
                  <div
                    className="h-full"
                    style={{
                      background:
                        s.hero === "—" || !s.hero
                          ? "rgba(80,80,80,0.3)"
                          : "rgba(245, 115, 27, 0.15)",
                    }}
                  />
                </div>
              );
            })}
          </div>
        )}
        {/* Dialogue cue markers */}
        {duration > 0 && dialogueCues.map((cue, i) => {
          const left = (cue.start / duration) * 100;
          const width = Math.max(0.5, (cue.duration / duration) * 100);
          return (
            <div
              key={`cue-${i}`}
              className="absolute bottom-1 h-1 bg-[var(--color-forge)] rounded"
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${cue.character}: ${cue.line ?? ""}`}
            />
          );
        })}
        {/* Playhead */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-[var(--color-paper)] pointer-events-none"
          style={{ left: `${progressPct}%` }}
        />
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2 px-3 py-2 bg-[var(--color-ink)] border-t border-white/5 text-sm">
        <button
          onClick={() => {
            const el = videoRef.current;
            if (el) el.paused ? el.play() : el.pause();
          }}
          className="px-3 py-1 rounded bg-[var(--color-forge)] text-[var(--color-ink)] font-medium hover:bg-[var(--color-ember)]"
        >
          {playing ? "Pause" : "Play"}
        </button>
        <button
          onClick={() => seek(Math.max(0, currentTime - frameDuration))}
          className="px-2 py-1 rounded border border-white/10 hover:bg-white/5"
          title=",  (prev frame)"
        >
          ◀ frame
        </button>
        <button
          onClick={() => seek(Math.min(duration, currentTime + frameDuration))}
          className="px-2 py-1 rounded border border-white/10 hover:bg-white/5"
          title=".  (next frame)"
        >
          frame ▶
        </button>
        <button
          onClick={() => setLooping((x) => !x)}
          className={`px-2 py-1 rounded border ${
            looping
              ? "border-[var(--color-forge)] text-[var(--color-forge)]"
              : "border-white/10 hover:bg-white/5"
          }`}
        >
          loop
        </button>
        <div className="flex gap-1 ml-2">
          {[0.25, 0.5, 1, 1.5, 2].map((r) => (
            <button
              key={r}
              onClick={() => setPlaybackRate(r)}
              className={`px-2 py-1 rounded text-xs ${
                playbackRate === r
                  ? "bg-[var(--color-forge)] text-[var(--color-ink)]"
                  : "border border-white/10 hover:bg-white/5"
              }`}
            >
              {r}x
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setMuted((m) => !m)}
            className="px-2 py-1 rounded border border-white/10 hover:bg-white/5"
          >
            {muted ? "unmute" : "mute"}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="w-24"
          />
        </div>
      </div>

      <div className="px-3 py-1 text-xs text-[var(--color-ash)] font-mono">
        <span className="text-[var(--color-mist)]">keys:</span> space play · j/l ±5s · ← → ±1s · shift+← ±10s · , . frame · m mute · L loop · 0 restart
      </div>
    </div>
  );
}

function formatTime(s: number): string {
  if (!isFinite(s) || s < 0) return "0:00.00";
  const mins = Math.floor(s / 60);
  const secs = s - mins * 60;
  return `${mins}:${secs.toFixed(2).padStart(5, "0")}`;
}
