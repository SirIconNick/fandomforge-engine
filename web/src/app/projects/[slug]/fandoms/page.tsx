import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ArtifactEditor from "@/components/ArtifactEditor";

type Params = Promise<{ slug: string }>;

export default async function FandomsEditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const seed = {
    schema_version: 1,
    fandoms: [],
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
        <h1>Fandoms (user-extensible knowledge)</h1>
      </div>
      <ArtifactEditor
        projectSlug={slug}
        artifactType="fandoms"
        seed={seed}
        title="fandoms.json"
        helpText="Custom fandom knowledge loaded into expert chat alongside the built-in prose memory. Add iconic scenes, visual language notes, and canonical songs for fandoms unique to this project. See templates/fandoms.json for an example shape."
      />
      <Link
        href={`/experts/chat/fandom-researcher?project=${slug}`}
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        Ask the fandom-researcher →
      </Link>
    </div>
  );
}
