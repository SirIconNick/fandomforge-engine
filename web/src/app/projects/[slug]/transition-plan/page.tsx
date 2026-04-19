import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ArtifactEditor from "@/components/ArtifactEditor";

type Params = Promise<{ slug: string }>;

export default async function TransitionPlanEditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const seed = {
    schema_version: 1,
    project_slug: slug,
    fps: 24,
    transitions: [],
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
        <h1>Transition plan</h1>
      </div>
      <ArtifactEditor
        projectSlug={slug}
        artifactType="transition-plan"
        seed={seed}
        title="transition-plan.json"
        helpText="The cut-to-cut language of your edit. Each transition links two shots and specifies type, timing (frames), and duration."
      />
      <Link
        href={`/experts/chat/transition-architect?project=${slug}`}
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        Ask the transition-architect →
      </Link>
    </div>
  );
}
