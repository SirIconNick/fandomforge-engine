import Link from "next/link";
import { notFound } from "next/navigation";
import { loadProjects } from "@/lib/fs";
import ProposeShotsClient from "./ProposeShotsClient";

type Params = Promise<{ slug: string }>;

export default async function ProposeShotsPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <Link
          href={`/projects/${slug}`}
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-4"
        >
          ← {project.name}
        </Link>
        <h1>Draft a shot list</h1>
        <p className="text-sm text-white/60 max-w-2xl">
          The shot-proposer reads your edit-plan, beat-map, and source catalog,
          then builds a first-draft shot list. Drops become hero shots, downbeats
          become cut points, empty catalog becomes placeholder shots you can
          swap later. The output lands as a reviewable patch — accept or reject
          each op before it writes to disk.
        </p>
      </div>
      <ProposeShotsClient projectSlug={slug} />
    </div>
  );
}
