import Link from "next/link";
import { notFound } from "next/navigation";
import fs from "node:fs/promises";
import path from "node:path";
import { PROJECT_ROOT, loadBeatMap, loadProjects } from "@/lib/fs";
import { MarkdownViewer } from "@/components/MarkdownViewer";
import { BeatMapVisualizer } from "@/components/BeatMapVisualizer";
import { UploadDropzone } from "@/components/UploadDropzone";
import { AssetBrowser } from "@/components/AssetBrowser";
import OnboardingTour from "@/components/OnboardingTour";
import UrlIngest from "@/components/UrlIngest";

type Params = Promise<{ slug: string }>;

async function readIfExists(p: string): Promise<string | null> {
  try {
    return await fs.readFile(p, "utf8");
  } catch {
    return null;
  }
}

export default async function ProjectDetailPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const projPath = path.join(PROJECT_ROOT, "projects", slug);
  const [editPlan, shotList, beatMapMd, beatMap] = await Promise.all([
    readIfExists(path.join(projPath, "edit-plan.md")),
    readIfExists(path.join(projPath, "shot-list.md")),
    readIfExists(path.join(projPath, "beat-map.md")),
    loadBeatMap(slug),
  ]);

  return (
    <div className="space-y-10">
      <OnboardingTour projectSlug={slug} />
      <div>
        <Link
          href="/projects"
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-4"
        >
          ← All projects
        </Link>
        <h1 className="capitalize">{project.name}</h1>
        {project.theme && (
          <p className="text-xl text-[var(--color-mist)] italic mt-2">
            &ldquo;{project.theme}&rdquo;
          </p>
        )}

        <div className="flex flex-wrap gap-2 mt-4 text-sm">
          <Link
            href={`/projects/${slug}/autopilot`}
            className="px-4 py-2 bg-[var(--color-forge)] text-[var(--color-ink)] rounded font-medium hover:bg-[var(--color-ember)]"
          >
            ✨ Auto-pilot
          </Link>
          <Link
            href={`/pipeline/${slug}`}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            Run pipeline (manual)
          </Link>
          <Link
            href={`/projects/${slug}/qa`}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            QA gate
          </Link>
          <Link
            href={`/editor/${slug}`}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            Timeline editor
          </Link>
          <Link
            href={`/player/${slug}`}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            Player
          </Link>
          <Link
            href={`/projects/${slug}/preview`}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            Rough preview
          </Link>
          <Link
            href={`/experts/chat/edit-strategist?project=${slug}`}
            className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            Ask an expert
          </Link>
          <Link
            href={`/projects/${slug}/propose-shots`}
            className="px-4 py-2 border border-[var(--color-forge)]/30 rounded hover:border-[var(--color-forge)]/60 text-[var(--color-forge)]"
          >
            ✨ Draft a shot list
          </Link>
        </div>

        <div className="flex flex-wrap gap-2 mt-3 text-xs">
          <span className="text-white/40 self-center mr-1">Edit artifacts:</span>
          <Link
            href={`/projects/${slug}/edit-plan`}
            className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            edit-plan
          </Link>
          <Link
            href={`/projects/${slug}/color-plan`}
            className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            color-plan
          </Link>
          <Link
            href={`/projects/${slug}/transition-plan`}
            className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            transition-plan
          </Link>
          <Link
            href={`/projects/${slug}/audio-plan`}
            className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            audio-plan
          </Link>
          <Link
            href={`/projects/${slug}/title-plan`}
            className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            title-plan
          </Link>
          <Link
            href={`/projects/${slug}/fandoms`}
            className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50"
          >
            fandoms
          </Link>
        </div>
      </div>

      <div id="upload" className="grid md:grid-cols-[1fr_320px] gap-6 scroll-mt-20">
        <section className="space-y-3">
          <h2>Add source media</h2>
          <UploadDropzone slug={slug} />
          <UrlIngest projectSlug={slug} />
        </section>
        <aside className="border border-white/10 rounded p-3 bg-white/[0.02]">
          <AssetBrowser slug={slug} />
        </aside>
      </div>

      {beatMap && (
        <section className="space-y-4">
          <h2>Beat map</h2>
          <BeatMapVisualizer data={beatMap} />
        </section>
      )}

      {editPlan && (
        <section className="space-y-4">
          <h2>Edit plan</h2>
          <div className="border border-white/10 rounded p-6">
            <MarkdownViewer content={editPlan} />
          </div>
        </section>
      )}

      {shotList && (
        <section className="space-y-4">
          <h2>Shot list</h2>
          <div className="border border-white/10 rounded p-6">
            <MarkdownViewer content={shotList} />
          </div>
        </section>
      )}

      {beatMapMd && !beatMap && (
        <section className="space-y-4">
          <h2>Beat map (markdown)</h2>
          <div className="border border-white/10 rounded p-6">
            <MarkdownViewer content={beatMapMd} />
          </div>
          <div className="text-sm text-[var(--color-ash)]">
            No <code className="text-[var(--color-ember)]">beat-map.json</code> yet. Run:{" "}
            <code className="text-[var(--color-ember)]">ff beat analyze &lt;song&gt; -o projects/{slug}/beat-map.json</code>
          </div>
        </section>
      )}
    </div>
  );
}
