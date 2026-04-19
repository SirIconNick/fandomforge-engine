"use client";

import { useEffect, useState } from "react";

interface FileEntry {
  name: string;
  path: string;
  size: number;
  ext?: string;
}

interface CatalogSource {
  id: string;
  path: string;
  fandom: string;
  title?: string;
  source_type?: string;
  year?: number;
  characters_present?: Array<{ character: string; appearances: number }>;
  flags?: Array<{ level: string; message: string }>;
}

interface AssetsResponse {
  catalog_sources: CatalogSource[];
  raw_files: FileEntry[];
  dialogue_files: FileEntry[];
  sfx_files: FileEntry[];
  export_files: FileEntry[];
}

export function AssetBrowser({ slug }: { slug: string }) {
  const [data, setData] = useState<AssetsResponse | null>(null);
  const [error, setError] = useState("");
  const [refreshAt, setRefreshAt] = useState(0);

  useEffect(() => {
    fetch(`/api/project/${slug}/assets`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return (await r.json()) as AssetsResponse;
      })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [slug, refreshAt]);

  if (error) {
    return (
      <div className="text-xs text-red-300 border border-red-500/30 rounded p-2">
        assets unavailable: {error}
      </div>
    );
  }
  if (!data) return <div className="text-sm text-white/50">loading assets…</div>;

  const byFandom = groupByFandom(data.catalog_sources);

  return (
    <div className="space-y-4 text-sm">
      <div className="flex items-center justify-between">
        <h3 className="font-serif text-lg">Assets</h3>
        <button
          onClick={() => setRefreshAt(Date.now())}
          className="text-xs px-2 py-1 rounded border border-white/10 hover:bg-white/5"
        >
          Refresh
        </button>
      </div>

      <Section title={`Indexed sources (${data.catalog_sources.length})`}>
        {Object.keys(byFandom).length === 0 ? (
          <p className="text-white/50 text-xs">
            Run <code className="font-mono bg-white/5 px-1 rounded">ff ingest</code> to
            populate source-catalog.json.
          </p>
        ) : (
          Object.entries(byFandom).map(([fandom, sources]) => (
            <div key={fandom} className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-white/50">
                {fandom} · {sources.length}
              </div>
              {sources.map((s) => (
                <div
                  key={s.id}
                  className="border border-white/10 rounded px-2 py-1 flex items-center justify-between gap-2"
                >
                  <div className="min-w-0">
                    <div className="truncate">
                      {s.title ?? s.path.split("/").pop()}
                      {s.year ? <span className="text-white/40"> ({s.year})</span> : null}
                    </div>
                    {s.characters_present && s.characters_present.length > 0 && (
                      <div className="text-[10px] text-white/50 truncate">
                        {s.characters_present.map((c) => c.character).join(", ")}
                      </div>
                    )}
                    {s.flags && s.flags.some((f) => f.level !== "info") && (
                      <div className="text-[10px] text-yellow-300 truncate">
                        {s.flags
                          .filter((f) => f.level !== "info")
                          .map((f) => f.message)
                          .join("; ")}
                      </div>
                    )}
                  </div>
                  <span className="text-[10px] text-white/30 font-mono truncate max-w-32">
                    {s.id.slice(0, 14)}
                  </span>
                </div>
              ))}
            </div>
          ))
        )}
      </Section>

      <Section title={`Raw media (${data.raw_files.length})`}>
        <FileList files={data.raw_files} />
      </Section>
      <Section title={`Dialogue (${data.dialogue_files.length})`}>
        <FileList files={data.dialogue_files} />
      </Section>
      <Section title={`SFX (${data.sfx_files.length})`}>
        <FileList files={data.sfx_files} />
      </Section>
      <Section title={`Exports (${data.export_files.length})`}>
        <FileList files={data.export_files} />
      </Section>
    </div>
  );
}

function groupByFandom(sources: CatalogSource[]): Record<string, CatalogSource[]> {
  const out: Record<string, CatalogSource[]> = {};
  for (const s of sources) {
    const key = s.fandom || "Unknown";
    (out[key] ?? (out[key] = [])).push(s);
  }
  return out;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="border border-white/10 rounded p-2 group" open>
      <summary className="cursor-pointer text-xs uppercase tracking-wide text-white/60 select-none">
        {title}
      </summary>
      <div className="mt-2 space-y-1">{children}</div>
    </details>
  );
}

function FileList({ files }: { files: FileEntry[] }) {
  if (!files.length) return <p className="text-white/40 text-xs">empty</p>;
  return (
    <ul className="space-y-0.5 text-xs font-mono">
      {files.map((f) => (
        <li key={f.path} className="flex justify-between gap-2">
          <span className="truncate">{f.name}</span>
          <span className="text-white/40 whitespace-nowrap">{formatBytes(f.size)}</span>
        </li>
      ))}
    </ul>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}M`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)}G`;
}
