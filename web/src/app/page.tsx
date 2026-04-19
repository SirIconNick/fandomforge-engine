import Link from "next/link";
import { loadExperts, loadProjects, loadKnowledgeDocs } from "@/lib/fs";

export default async function HomePage() {
  const [experts, projects, docs] = await Promise.all([
    loadExperts(),
    loadProjects(),
    loadKnowledgeDocs(),
  ]);

  return (
    <div className="space-y-20">
      <section className="space-y-6">
        <div className="inline-block px-3 py-1 border border-[var(--color-forge)] text-[var(--color-forge)] text-xs tracking-widest uppercase rounded-full">
          Multifandom Video AI Suite
        </div>
        <h1 className="max-w-4xl">
          From a song and a theme to a finished edit plan, without fighting a blank timeline.
        </h1>
        <p className="text-xl text-[var(--color-mist)] max-w-2xl">
          Ten specialized AI experts, a Python analysis toolkit, a beat mapper, a
          knowledge base, and project templates. All wired together so you can focus
          on the creative work.
        </p>
        <div className="flex flex-wrap gap-3 pt-4">
          <Link
            href="/projects"
            className="px-5 py-3 bg-[var(--color-forge)] text-[var(--color-ink)] rounded font-medium hover:bg-[var(--color-ember)] transition-colors"
          >
            Your projects
          </Link>
          <Link
            href="/experts"
            className="px-5 py-3 border border-white/20 rounded hover:bg-white/5 transition-colors"
          >
            Meet the experts
          </Link>
          <Link
            href="/beat-map"
            className="px-5 py-3 border border-white/20 rounded hover:bg-white/5 transition-colors"
          >
            Beat mapper
          </Link>
        </div>
      </section>

      <section>
        <div className="flex items-baseline justify-between mb-6">
          <h2>Experts</h2>
          <Link href="/experts" className="text-sm text-[var(--color-forge)] hover:underline">
            View all →
          </Link>
        </div>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {experts.slice(0, 6).map((e) => (
            <Link
              key={e.slug}
              href={`/experts/${e.slug}`}
              className="block p-5 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors group"
            >
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: colorMap[e.color] ?? "#888" }}
                />
                <div className="font-mono text-xs text-[var(--color-ash)] uppercase tracking-wider">
                  {e.slug}
                </div>
              </div>
              <div className="font-display text-xl mb-2 group-hover:text-[var(--color-forge)] transition-colors">
                {e.name}
              </div>
              <div className="text-sm text-[var(--color-mist)] line-clamp-3">
                {e.description.split(".")[0]}.
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section>
        <div className="flex items-baseline justify-between mb-6">
          <h2>Your projects</h2>
          <Link href="/projects" className="text-sm text-[var(--color-forge)] hover:underline">
            View all →
          </Link>
        </div>
        {projects.length === 0 ? (
          <div className="p-8 border border-dashed border-white/10 rounded text-center text-[var(--color-mist)]">
            No projects yet. Create your first one:
            <pre className="inline-block ml-2 px-2 py-1 bg-white/5 rounded text-[var(--color-ember)]">
              ff project new my-edit
            </pre>
          </div>
        ) : (
          <div className="grid sm:grid-cols-2 gap-4">
            {projects.slice(0, 4).map((p) => (
              <Link
                key={p.slug}
                href={`/projects/${p.slug}`}
                className="block p-5 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
              >
                <div className="font-display text-xl capitalize mb-1">{p.name}</div>
                <div className="text-sm text-[var(--color-mist)] mb-3">
                  {p.theme ?? "No theme set"}
                </div>
                <div className="flex gap-2 text-xs">
                  {p.hasEditPlan && <span className="px-2 py-0.5 bg-white/5 rounded">plan</span>}
                  {p.hasShotList && <span className="px-2 py-0.5 bg-white/5 rounded">shots</span>}
                  {p.hasBeatMap && (
                    <span className="px-2 py-0.5 bg-[var(--color-forge)]/20 text-[var(--color-forge)] rounded">
                      beat map
                    </span>
                  )}
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="flex items-baseline justify-between mb-6">
          <h2>Knowledge base</h2>
          <Link href="/knowledge" className="text-sm text-[var(--color-forge)] hover:underline">
            View all {docs.length} →
          </Link>
        </div>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {docs.slice(0, 6).map((d) => (
            <Link
              key={d.slug}
              href={`/knowledge/${d.slug}`}
              className="block p-4 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
            >
              <div className="font-display text-lg">{d.title}</div>
              <div className="font-mono text-xs text-[var(--color-ash)] mt-1">{d.slug}</div>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

const colorMap: Record<string, string> = {
  gold: "#f2b32d",
  red: "#e5484d",
  purple: "#8e4ec6",
  orange: "#f5731b",
  teal: "#12a594",
  blue: "#3e63dd",
  emerald: "#30a46c",
  cyan: "#05a2c2",
  magenta: "#e93d82",
  pink: "#e93d82",
};
