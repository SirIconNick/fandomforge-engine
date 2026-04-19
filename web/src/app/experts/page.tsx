import Link from "next/link";
import { loadExperts } from "@/lib/fs";

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

export default async function ExpertsPage() {
  const experts = await loadExperts();
  return (
    <div className="space-y-10">
      <header className="space-y-4">
        <h1>The experts</h1>
        <p className="text-xl text-[var(--color-mist)] max-w-2xl">
          Ten specialists covering every dimension of multifandom video creation.
          Each owns one domain and hands off cleanly to the others.
        </p>
      </header>

      <div className="grid sm:grid-cols-2 gap-4">
        {experts.map((e) => (
          <Link
            key={e.slug}
            href={`/experts/${e.slug}`}
            className="block p-6 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors group"
          >
            <div className="flex items-center gap-2 mb-3">
              <span
                className="h-3 w-3 rounded-full"
                style={{ backgroundColor: colorMap[e.color] ?? "#888" }}
              />
              <div className="font-mono text-xs text-[var(--color-ash)] uppercase tracking-wider">
                {e.slug}
              </div>
            </div>
            <div className="font-display text-2xl mb-2 group-hover:text-[var(--color-forge)] transition-colors">
              {e.name}
            </div>
            <p className="text-sm text-[var(--color-mist)] line-clamp-4">
              {e.description.split("Examples")[0].trim()}
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
