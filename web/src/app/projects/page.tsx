import Link from "next/link";
import { loadProjects } from "@/lib/fs";

export default async function ProjectsPage() {
  const projects = await loadProjects();

  return (
    <div className="space-y-10">
      <header className="space-y-4 flex items-start justify-between">
        <div>
          <h1>Your projects</h1>
          <p className="text-xl text-[var(--color-mist)] max-w-2xl">
            Every edit in progress. Each project has a plan, a beat map, a shot list,
            and the working state of your edit.
          </p>
        </div>
        <Link
          href="/projects/new"
          className="px-4 py-2 rounded bg-[var(--color-forge)] text-black font-semibold"
        >
          New project
        </Link>
      </header>

      {projects.length === 0 ? (
        <div className="p-12 border border-dashed border-white/10 rounded text-center space-y-4">
          <div className="text-[var(--color-mist)]">No projects yet.</div>
          <Link
            href="/projects/new"
            className="inline-block px-4 py-2 rounded bg-[var(--color-forge)] text-black"
          >
            Start a new project
          </Link>
          <div className="text-xs text-[var(--color-ash)] pt-2">
            or from CLI: <code className="font-mono bg-white/5 px-1 rounded">ff project new my-first-edit</code>
          </div>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 gap-4">
          {projects.map((p) => (
            <Link
              key={p.slug}
              href={`/projects/${p.slug}`}
              className="block p-6 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
            >
              <div className="font-display text-2xl capitalize mb-2">{p.name}</div>
              {p.theme && (
                <div className="text-sm text-[var(--color-mist)] italic mb-4">
                  &ldquo;{p.theme}&rdquo;
                </div>
              )}
              <div className="flex gap-2 flex-wrap text-xs">
                {p.hasEditPlan && (
                  <span className="px-2 py-0.5 bg-white/5 rounded">edit plan</span>
                )}
                {p.hasShotList && (
                  <span className="px-2 py-0.5 bg-white/5 rounded">shot list</span>
                )}
                {p.hasBeatMap && (
                  <span className="px-2 py-0.5 bg-[var(--color-forge)]/20 text-[var(--color-forge)] rounded">
                    beat map
                  </span>
                )}
              </div>
              <div className="text-xs text-[var(--color-ash)] mt-3">
                Updated {new Date(p.updatedAt).toLocaleDateString()}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
