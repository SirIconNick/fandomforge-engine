import Link from "next/link";
import { loadKnowledgeDocs } from "@/lib/fs";

export default async function KnowledgePage() {
  const docs = await loadKnowledgeDocs();
  return (
    <div className="space-y-10">
      <header className="space-y-4">
        <h1>Knowledge base</h1>
        <p className="text-xl text-[var(--color-mist)] max-w-2xl">
          Deep reference on every technique, theory, and practical consideration
          for multifandom video creation.
        </p>
      </header>

      <div className="grid sm:grid-cols-2 gap-3">
        {docs.map((d) => (
          <Link
            key={d.slug}
            href={`/knowledge/${d.slug}`}
            className="block p-5 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
          >
            <div className="font-display text-xl">{d.title}</div>
            <div className="font-mono text-xs text-[var(--color-ash)] mt-1">{d.slug}.md</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
