import fs from "node:fs/promises";
import path from "node:path";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PROJECT_ROOT, loadProjects } from "@/lib/fs";
import { PipelineRunner } from "@/components/PipelineRunner";

type Params = Promise<{ slug: string }>;

async function listFiles(dir: string, exts: string[]): Promise<string[]> {
  try {
    const entries = await fs.readdir(dir);
    return entries.filter((e) =>
      exts.some((x) => e.toLowerCase().endsWith(x.toLowerCase())),
    );
  } catch {
    return [];
  }
}

async function listMarkdownFiles(dir: string, prefix: string = ""): Promise<string[]> {
  try {
    const entries = await fs.readdir(dir);
    return entries.filter((e) => e.toLowerCase().endsWith(".md") && e.startsWith(prefix));
  } catch {
    return [];
  }
}

async function listFilesFromMultipleDirs(dirs: string[], exts: string[]): Promise<string[]> {
  const results: string[] = [];
  for (const dir of dirs) {
    try {
      const entries = await fs.readdir(dir);
      for (const e of entries) {
        if (exts.some((x) => e.toLowerCase().endsWith(x.toLowerCase()))) {
          if (!results.includes(e)) results.push(e);
        }
      }
    } catch {
      // skip missing dir
    }
  }
  return results;
}

export default async function PipelineProject({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const projDir = path.join(PROJECT_ROOT, "projects", slug);
  const rawDir = path.join(projDir, "raw");

  // Look in root, plans/, demos/, and data/ — so the dropdowns always see everything
  const [shotLists, songs, dialogueScripts, colorPlans] = await Promise.all([
    listFilesFromMultipleDirs(
      [projDir, path.join(projDir, "plans"), path.join(projDir, "demos")],
      [".md"],
    ).then((entries) => entries.filter((e) => e.toLowerCase().startsWith("shot-list"))),
    listFiles(rawDir, [".mp3", ".wav", ".m4a", ".flac", ".aac"]),
    listFilesFromMultipleDirs(
      [projDir, path.join(projDir, "data"), path.join(projDir, "demos")],
      [".json"],
    ).then((entries) => entries.filter((e) => e.toLowerCase().includes("dialogue"))),
    listFilesFromMultipleDirs(
      [projDir, path.join(projDir, "data"), path.join(projDir, "demos")],
      [".json"],
    ).then((entries) => entries.filter((e) => e.toLowerCase().includes("color"))),
  ]);

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/pipeline"
          className="inline-block text-sm text-[var(--color-forge)] hover:underline mb-3"
        >
          ← All projects
        </Link>
        <h1 className="capitalize">{project.name}</h1>
        {project.theme && (
          <p className="text-lg text-[var(--color-mist)] italic mt-2">
            &ldquo;{project.theme}&rdquo;
          </p>
        )}
      </div>

      <div className="flex gap-3 text-sm">
        <Link
          href={`/editor/${slug}`}
          className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
        >
          Timeline editor →
        </Link>
        <Link
          href={`/player/${slug}`}
          className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
        >
          Player →
        </Link>
        <Link
          href={`/projects/${slug}`}
          className="px-4 py-2 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
        >
          Project docs →
        </Link>
      </div>

      <section className="border border-white/10 rounded p-6">
        <h2 className="text-xl mb-4">Run the pipeline</h2>
        <PipelineRunner
          project={slug}
          availableShotLists={shotLists}
          availableSongs={songs}
          availableDialogueScripts={dialogueScripts}
          availableColorPlans={colorPlans}
        />
      </section>

      <section className="border border-white/5 rounded p-5 text-sm space-y-2 bg-white/[0.02]">
        <div className="font-semibold mb-2">Quick reference</div>
        <div className="text-[var(--color-mist)]">
          • <strong>Shot lists</strong> ({shotLists.length}): {shotLists.join(", ") || "none"}
        </div>
        <div className="text-[var(--color-mist)]">
          • <strong>Songs in raw/</strong> ({songs.length}): {songs.join(", ") || "none — run ff sources download or drop a song into raw/"}
        </div>
        <div className="text-[var(--color-mist)]">
          • <strong>Dialogue JSONs</strong> ({dialogueScripts.length}):{" "}
          {dialogueScripts.join(", ") || "none — run ff dialogue parse --project " + slug}
        </div>
        <div className="text-[var(--color-mist)]">
          • <strong>Color plans</strong> ({colorPlans.length}): {colorPlans.join(", ") || "none — run ff color-plan init --project " + slug}
        </div>
      </section>
    </div>
  );
}
