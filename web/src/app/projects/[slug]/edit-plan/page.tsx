import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ArtifactEditor from "@/components/ArtifactEditor";

type Params = Promise<{ slug: string }>;

export default async function EditPlanEditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const seed = {
    schema_version: 1,
    project_slug: slug,
    concept: {
      theme: "",
      one_sentence: "",
    },
    song: {
      title: "",
      artist: "",
    },
    fandoms: [],
    acts: [],
    generated_at: new Date().toISOString(),
    generator: "web:ArtifactEditor",
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
        <h1>Edit plan</h1>
      </div>
      <ArtifactEditor
        projectSlug={slug}
        artifactType="edit-plan"
        seed={seed}
        title="edit-plan.json"
        helpText="The master artifact. Theme, song, fandoms, and act breakdown. Everything else references this."
      />
      <Link
        href={`/experts/chat/edit-strategist?project=${slug}`}
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        Ask the edit-strategist →
      </Link>
    </div>
  );
}
