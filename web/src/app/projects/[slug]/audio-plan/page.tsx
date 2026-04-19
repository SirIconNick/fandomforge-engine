import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ArtifactEditor from "@/components/ArtifactEditor";

type Params = Promise<{ slug: string }>;

export default async function AudioPlanEditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const seed = {
    schema_version: 1,
    project_slug: slug,
    song: {
      title: "",
      artist: "",
      source_path: "",
    },
    sfx_layers: [],
    vo_cues: [],
    mix: {
      target_lufs: -14,
      song_db: 0,
      dialogue_db: 3,
      sfx_db: -6,
    },
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
        <h1>Audio plan</h1>
      </div>
      <ArtifactEditor
        projectSlug={slug}
        artifactType="audio-plan"
        seed={seed}
        title="audio-plan.json"
        helpText="Song, SFX layers, voice-over cues, and mix targets. YouTube loudness is typically -14 LUFS."
      />
      <Link
        href={`/experts/chat/audio-producer?project=${slug}`}
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        Ask the audio-producer →
      </Link>
    </div>
  );
}
