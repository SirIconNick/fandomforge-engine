"use client";

import { useCallback, useState } from "react";

interface UploadResult {
  saved: Array<{ name: string; bytes: number; path: string }>;
  rejected: Array<{ name: string; reason: string }>;
  target_dir: string;
}

const TARGET_DIRS = [
  { value: "raw", label: "Raw sources (videos)" },
  { value: "dialogue", label: "Dialogue / VO" },
  { value: "sfx", label: "SFX" },
  { value: "references", label: "Reference stills" },
  { value: "luts", label: "LUTs (.cube)" },
];

export function UploadDropzone({ slug }: { slug: string }) {
  const [target, setTarget] = useState("raw");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string>("");
  const [dragging, setDragging] = useState(false);

  const onUpload = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;

      setError("");
      setResult(null);
      setUploading(true);
      setProgress(0);

      const fd = new FormData();
      for (const f of list) fd.append("file", f);
      fd.append("target_dir", target);

      try {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `/api/project/${slug}/upload`);
        await new Promise<void>((resolve, reject) => {
          xhr.upload.onprogress = (ev) => {
            if (ev.lengthComputable) {
              setProgress(Math.round((ev.loaded / ev.total) * 100));
            }
          };
          xhr.onload = () => {
            try {
              const body = JSON.parse(xhr.responseText) as
                | UploadResult
                | { error: string };
              if (xhr.status >= 200 && xhr.status < 300) {
                setResult(body as UploadResult);
                resolve();
              } else {
                reject(new Error((body as { error: string }).error ?? xhr.statusText));
              }
            } catch (e) {
              reject(e);
            }
          };
          xhr.onerror = () => reject(new Error("Network error"));
          xhr.send(fd);
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setUploading(false);
      }
    },
    [slug, target]
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) onUpload(e.dataTransfer.files);
      }}
      className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
        dragging
          ? "border-[var(--color-forge,#ff5a1f)] bg-[var(--color-forge,#ff5a1f)]/10"
          : "border-white/15 bg-white/[0.02]"
      }`}
    >
      <div className="flex items-center justify-between gap-3 mb-3">
        <label className="text-xs uppercase tracking-wide text-white/60">Upload to</label>
        <select
          className="bg-black/40 border border-white/10 rounded px-2 py-1 text-sm"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        >
          {TARGET_DIRS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      </div>
      <p className="mb-4 text-sm text-white/70">
        Drop files here, or select:
      </p>
      <input
        type="file"
        multiple
        className="block mx-auto text-sm file:mr-3 file:px-3 file:py-1 file:rounded file:border-0 file:bg-white/10 file:text-white hover:file:bg-white/20"
        onChange={(e) => {
          if (e.target.files) onUpload(e.target.files);
        }}
        disabled={uploading}
      />

      {uploading && (
        <div className="mt-4">
          <div className="h-2 bg-white/10 rounded overflow-hidden">
            <div
              className="h-full bg-[var(--color-forge,#ff5a1f)] transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-xs text-white/60 mt-1">{progress}% uploaded</p>
        </div>
      )}

      {error && (
        <p className="mt-3 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-3 text-left text-sm space-y-2">
          {result.saved.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wide text-green-300/80 mb-1">
                Saved ({result.saved.length})
              </div>
              <ul className="space-y-1 text-green-200/80">
                {result.saved.map((s) => (
                  <li key={s.path} className="font-mono text-xs truncate">
                    {s.name} — {formatBytes(s.bytes)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.rejected.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wide text-red-300/80 mb-1">
                Rejected ({result.rejected.length})
              </div>
              <ul className="space-y-1 text-red-200/80">
                {result.rejected.map((r) => (
                  <li key={r.name} className="font-mono text-xs">
                    {r.name} — {r.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
