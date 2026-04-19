"use client";

import { useState } from "react";

type Mode = "both" | "video" | "audio";
type Browser =
  | "" | "chrome" | "chromium" | "brave" | "edge"
  | "firefox" | "safari" | "opera" | "vivaldi" | "whale";

const BROWSER_OPTIONS: readonly { value: Browser; label: string }[] = [
  { value: "", label: "none (public content)" },
  { value: "chrome", label: "Chrome" },
  { value: "chromium", label: "Chromium" },
  { value: "brave", label: "Brave" },
  { value: "edge", label: "Edge" },
  { value: "firefox", label: "Firefox" },
  { value: "safari", label: "Safari" },
  { value: "opera", label: "Opera" },
  { value: "vivaldi", label: "Vivaldi" },
  { value: "whale", label: "Whale" },
];

export interface UrlIngestProps {
  projectSlug: string;
}

const MODE_LABELS: Record<Mode, string> = {
  both: "video + audio",
  video: "video only",
  audio: "audio only",
};

const MODE_HINTS: Record<Mode, string> = {
  both: "best video + best audio, merged mp4, written to raw/ and auto-ingested.",
  video: "video stream only, silent mp4, written to raw/. No ingest.",
  audio: "audio extracted to chosen format, written to assets/. No ingest.",
};

export default function UrlIngest({ projectSlug }: UrlIngestProps) {
  const [mode, setMode] = useState<Mode>("both");
  const [url, setUrl] = useState("");
  const [resolution, setResolution] = useState("1080");
  const [audioFormat, setAudioFormat] = useState("mp3");
  const [filename, setFilename] = useState("");
  const [note, setNote] = useState("");
  const [browser, setBrowser] = useState<Browser>("");
  const [status, setStatus] = useState<"idle" | "running" | "ok" | "error">("idle");
  const [log, setLog] = useState("");
  const [error, setError] = useState("");

  async function submit() {
    if (!url.trim()) return;
    setStatus("running");
    setError("");
    setLog("");
    try {
      const res = await fetch("/api/grab", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_slug: projectSlug,
          url: url.trim(),
          mode,
          resolution: mode !== "audio" ? resolution : undefined,
          audio_format: mode === "audio" ? audioFormat : undefined,
          filename: filename.trim() || undefined,
          note: note.trim() || undefined,
          cookies_from_browser: browser || undefined,
        }),
      });
      const body = await res.json();
      if (!res.ok || !body.ok) {
        setStatus("error");
        setError(body.error ?? `grab failed (${res.status})`);
        setLog((body.stdout ?? "") + "\n" + (body.stderr ?? ""));
        return;
      }
      setStatus("ok");
      setLog(body.log ?? "");
      setUrl("");
    } catch (e) {
      setStatus("error");
      setError((e as Error).message);
    }
  }

  return (
    <div className="space-y-3 border border-white/10 rounded p-4 bg-white/[0.02]">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Grab from URL</h3>
        <div
          role="tablist"
          aria-label="Download mode"
          className="inline-flex border border-white/10 rounded text-xs"
        >
          {(Object.keys(MODE_LABELS) as Mode[]).map((m, i) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={`px-2 py-1 ${i > 0 ? "border-l border-white/10" : ""} ${
                mode === m ? "bg-white/10" : ""
              }`}
            >
              {MODE_LABELS[m]}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs text-white/60">
        Any yt-dlp-supported URL: YouTube, Vimeo, Archive.org, direct media links, etc.
        No license gating — you decide what you want.
      </p>
      <p className="text-[11px] text-white/50 italic">{MODE_HINTS[mode]}</p>

      <input
        type="url"
        placeholder="https://www.youtube.com/watch?v=..."
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 text-sm font-mono"
      />

      <div className="grid grid-cols-2 gap-2">
        {mode !== "audio" && (
          <label className="text-xs">
            <span className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
              Resolution
            </span>
            <select
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5"
            >
              <option value="1080">1080p</option>
              <option value="720">720p</option>
              <option value="480">480p</option>
              <option value="best">best</option>
            </select>
          </label>
        )}
        {mode === "audio" && (
          <label className="text-xs">
            <span className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
              Audio format
            </span>
            <select
              value={audioFormat}
              onChange={(e) => setAudioFormat(e.target.value)}
              className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5"
            >
              <option value="mp3">mp3</option>
              <option value="m4a">m4a</option>
              <option value="flac">flac</option>
              <option value="wav">wav</option>
              <option value="opus">opus</option>
            </select>
          </label>
        )}
        <label className="text-xs">
          <span className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
            Filename (optional)
          </span>
          <input
            type="text"
            placeholder={mode === "audio" ? "song" : "auto"}
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5"
          />
        </label>
      </div>

      <label className="text-xs block">
        <span className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
          Note (optional, saved to sidecar)
        </span>
        <input
          type="text"
          placeholder="e.g. official trailer, season 2 episode 4, etc."
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5"
        />
      </label>

      <label className="text-xs block">
        <span className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
          Auth cookies (only needed for age-restricted / private content)
        </span>
        <select
          value={browser}
          onChange={(e) => setBrowser(e.target.value as Browser)}
          className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5"
        >
          {BROWSER_OPTIONS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
      </label>

      {error && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 whitespace-pre-wrap">
          {error}
        </div>
      )}
      {log && (
        <details className="text-[10px]">
          <summary className="cursor-pointer text-white/60">log</summary>
          <pre className="bg-black/40 border border-white/10 rounded p-2 mt-1 max-h-40 overflow-auto whitespace-pre-wrap">
            {log}
          </pre>
        </details>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={submit}
          disabled={status === "running" || !url.trim()}
          className="px-3 py-1.5 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-xs disabled:opacity-40"
        >
          {status === "running" ? "downloading…" : `Grab ${MODE_LABELS[mode]}`}
        </button>
        {status === "ok" && <span className="text-xs text-green-300">done</span>}
      </div>
    </div>
  );
}
