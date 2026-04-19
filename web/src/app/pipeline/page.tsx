import { loadProjects } from "@/lib/fs";
import Link from "next/link";

export default async function PipelineIndex() {
  const projects = await loadProjects();
  return (
    <div className="space-y-10">
      <header className="space-y-3">
        <h1>Pipeline</h1>
        <p className="text-xl text-[var(--color-mist)] max-w-2xl">
          Start a rough-cut run for any project and watch it build in real time.
          Select a project below to configure and run.
        </p>
      </header>

      {projects.length === 0 ? (
        <div className="p-10 border border-dashed border-white/10 rounded text-center">
          <div className="text-[var(--color-mist)] mb-4">No projects yet.</div>
          <div className="font-mono text-sm p-2 bg-white/5 rounded inline-block">
            ff project new my-first-edit
          </div>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 gap-4">
          {projects.map((p) => (
            <Link
              key={p.slug}
              href={`/pipeline/${p.slug}`}
              className="block p-6 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
            >
              <div className="font-display text-2xl capitalize mb-1">{p.name}</div>
              {p.theme && (
                <div className="text-sm italic text-[var(--color-mist)] mb-3">
                  &ldquo;{p.theme}&rdquo;
                </div>
              )}
              <div className="text-xs text-[var(--color-ember)]">
                Configure → run → watch →
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
