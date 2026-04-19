"use client";

import { useState } from "react";

type Kind = "video" | "song";

export interface UrlIngestProps {
  projectSlug: string;
}

export default function UrlIngest({ projectSlug }: UrlIngestProps) {
  const [kind, setKind] = useState<Kind>("video");
  const [url, setUrl] = useState("");
  const [license, setLicense] = useState("");
  const [resolution, setResolution] = useState("1080");
  const [filename, setFilename] = useState("");
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
          kind,
          license_note: license.trim() || undefined,
          resolution: kind === "video" ? resolution : undefined,
          filename: filename.trim() || undefined,
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

  const needsLicense =
    /youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com/i.test(url) && !license.trim();

  return (
    <div className="space-y-3 border border-white/10 rounded p-4 bg-white/[0.02]">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Grab from URL</h3>
        <div
          role="tablist"
          aria-label="Download kind"
          className="inline-flex border border-white/10 rounded text-xs"
        >
          <button
            role="tab"
            aria-selected={kind === "video"}
            onClick={() => setKind("video")}
            className={`px-2 py-1 ${kind === "video" ? "bg-white/10" : ""}`}
          >
            video
          </button>
          <button
            role="tab"
            aria-selected={kind === "song"}
            onClick={() => setKind("song")}
            className={`px-2 py-1 border-l border-white/10 ${
              kind === "song" ? "bg-white/10" : ""
            }`}
          >
            song
          </button>
        </div>
      </div>
      <p className="text-xs text-white/60">
        Any yt-dlp-supported URL: YouTube, Vimeo, Archive.org, direct mp4/mp3 links.
        Streaming services (Netflix, Disney+, etc.) are blocked by the denylist.
      </p>

      <input
        type="url"
        placeholder="https://www.youtube.com/watch?v=..."
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 text-sm font-mono"
      />

      {(needsLicense ||
        /youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com/i.test(url)) && (
        <div>
          <label className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
            License note (required for YouTube / Vimeo / Dailymotion)
          </label>
          <input
            type="text"
            placeholder='e.g. "Official studio trailer — fair use for transformative editing"'
            value={license}
            onChange={(e) => setLicense(e.target.value)}
            className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 text-xs"
          />
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        {kind === "video" && (
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
        <label className="text-xs">
          <span className="block text-[10px] uppercase tracking-wide text-white/50 mb-1">
            Filename (optional)
          </span>
          <input
            type="text"
            placeholder={kind === "song" ? "song" : "auto"}
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5"
          />
        </label>
      </div>

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
          disabled={
            status === "running" ||
            !url.trim() ||
            (needsLicense && !license.trim())
          }
          className="px-3 py-1.5 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-xs disabled:opacity-40"
        >
          {status === "running" ? "downloading…" : `Grab ${kind}`}
        </button>
        {status === "ok" && <span className="text-xs text-green-300">done</span>}
      </div>
    </div>
  );
}
