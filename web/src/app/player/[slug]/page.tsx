import fs from "node:fs/promises";
import path from "node:path";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PROJECT_ROOT, loadProjects } from "@/lib/fs";
import { PlayerShell } from "./PlayerShell";

type Params = Promise<{ slug: string }>;

export default async function PlayerPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const exportsDir = path.join(PROJECT_ROOT, "projects", slug, "exports");
  const videos = await fs
    .readdir(exportsDir)
    .then((entries) =>
      entries.filter((e) =>
        [".mp4", ".mov", ".mkv", ".webm"].some((x) =>
          e.toLowerCase().endsWith(x),
        ),
      ),
    )
    .catch(() => []);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/projects/${slug}`}
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-3"
        >
          ← {project.name}
        </Link>
        <h1 className="capitalize">Player — {project.name}</h1>
      </div>

      {videos.length === 0 ? (
        <div className="p-10 border border-dashed border-white/10 rounded text-center text-[var(--color-mist)]">
          No videos in{" "}
          <code className="text-[var(--color-ember)]">
            projects/{slug}/exports/
          </code>{" "}
          yet. Run the pipeline first.
          <div className="mt-3">
            <Link
              href={`/pipeline/${slug}`}
              className="inline-block px-4 py-2 bg-[var(--color-forge)] text-[var(--color-ink)] rounded text-sm font-medium hover:bg-[var(--color-ember)]"
            >
              Go to pipeline →
            </Link>
          </div>
        </div>
      ) : (
        <PlayerShell project={slug} videos={videos} />
      )}
    </div>
  );
}
