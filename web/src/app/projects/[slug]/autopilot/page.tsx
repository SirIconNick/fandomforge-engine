import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import AutopilotProgress from "@/components/AutopilotProgress";

type Params = Promise<{ slug: string }>;

export default async function AutopilotPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  return (
    <div className="space-y-4 max-w-3xl">
      <div>
        <Link
          href={`/projects/${slug}`}
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-4"
        >
          ← {project.name}
        </Link>
        <h1>Auto-pilot</h1>
        <p className="text-sm text-white/60 max-w-2xl">
          Runs the full pipeline in one shot: beat analysis → edit plan draft →
          shot list → emotion arc → QA gate. Each step is idempotent and
          resumable — if something fails, fix it and restart. Progress is
          journaled to <code className="font-mono text-xs">.history/autopilot.jsonl</code>.
        </p>
      </div>
      <AutopilotProgress projectSlug={slug} />
    </div>
  );
}
