import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ArtifactEditor from "@/components/ArtifactEditor";

type Params = Promise<{ slug: string }>;

export default async function TitlePlanEditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const seed = {
    schema_version: 1,
    project_slug: slug,
    fps: 24,
    resolution: { width: 1920, height: 1080 },
    titles: [],
  };

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/projects/${slug}`}
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-4"
        >
          ← {project.name}
        </Link>
        <h1>Title plan</h1>
      </div>
      <ArtifactEditor
        projectSlug={slug}
        artifactType="title-plan"
        seed={seed}
        title="title-plan.json"
        helpText="Title cards, kinetic lyrics, and on-screen text. Keep text restrained — often less is more."
      />
      <Link
        href={`/experts/chat/title-designer?project=${slug}`}
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        Ask the title-designer →
      </Link>
    </div>
  );
}
