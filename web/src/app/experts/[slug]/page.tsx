import Link from "next/link";
import { notFound } from "next/navigation";
import { loadExperts } from "@/lib/fs";
import { MarkdownViewer } from "@/components/MarkdownViewer";

export async function generateStaticParams() {
  const experts = await loadExperts();
  return experts.map((e) => ({ slug: e.slug }));
}

type Params = Promise<{ slug: string }>;

export default async function ExpertPage({ params }: { params: Params }) {
  const { slug } = await params;
  const experts = await loadExperts();
  const expert = experts.find((e) => e.slug === slug);
  if (!expert) notFound();

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/experts"
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-4"
        >
          ← All experts
        </Link>
        <div className="flex items-center gap-3 mb-2">
          <span
            className="h-4 w-4 rounded-full"
            style={{ backgroundColor: colorMap[expert.color] ?? "#888" }}
          />
          <div className="font-mono text-xs text-[var(--color-ash)] uppercase tracking-wider">
            {expert.slug}
          </div>
        </div>
        <h1>{expert.name}</h1>
        <p className="mt-4 text-lg text-[var(--color-mist)] max-w-3xl">
          {expert.description.split("Examples")[0].trim()}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Link
          href={`/experts/chat/${expert.slug}`}
          className="px-4 py-2 rounded bg-[var(--color-forge)] text-[var(--color-ink)] font-semibold"
        >
          Chat with this expert
        </Link>
        <div className="px-4 py-2 border border-white/10 rounded bg-white/5 text-sm">
          <span className="text-xs uppercase tracking-wider text-[var(--color-ash)] mr-2">
            Claude Code
          </span>
          <code className="text-[var(--color-ember)]">@{expert.slug}</code>
        </div>
      </div>

      <div className="border-t border-white/5 pt-8">
        <MarkdownViewer content={expert.content} />
      </div>
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
