import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ArtifactEditor from "@/components/ArtifactEditor";

type Params = Promise<{ slug: string }>;

export default async function ColorPlanEditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const seed = {
    schema_version: 1,
    project_slug: slug,
    target_color_space: "Rec.709",
    global_lut_intensity: 1,
    per_source: {},
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
        <h1>Color plan</h1>
      </div>
      <ArtifactEditor
        projectSlug={slug}
        artifactType="color-plan"
        seed={seed}
        title="color-plan.json"
        helpText="Per-source color adjustments, global LUT, and hero frame reference. Save writes atomically and records a journal entry that the color-grader can audit."
      />
      <Link
        href={`/experts/chat/color-grader?project=${slug}`}
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        Ask the color-grader →
      </Link>
    </div>
  );
}
