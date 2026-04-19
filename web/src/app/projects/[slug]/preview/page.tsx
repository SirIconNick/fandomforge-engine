import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import EditRoughPreview from "@/components/EditRoughPreview";

type Params = Promise<{ slug: string }>;

export default async function PreviewPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  return (
    <div className="space-y-4">
      <div>
        <Link
          href={`/projects/${slug}`}
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-4"
        >
          ← {project.name}
        </Link>
        <h1>Rough preview</h1>
        <p className="text-sm text-white/60 max-w-2xl">
          Plays your shot-list against the song audio without running the full
          render pipeline. Missing sources render as placeholders — ingest
          videos into <code className="font-mono text-xs">raw/</code> to see them inline.
        </p>
      </div>
      <EditRoughPreview projectSlug={slug} />
    </div>
  );
}
