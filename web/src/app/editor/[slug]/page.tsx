import fs from "node:fs/promises";
import path from "node:path";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PROJECT_ROOT, loadProjects } from "@/lib/fs";
import { EditorShell } from "./EditorShell";

type Params = Promise<{ slug: string }>;

export default async function EditorPage({ params }: { params: Params }) {
  const { slug } = await params;
  const projects = await loadProjects();
  const project = projects.find((p) => p.slug === slug);
  if (!project) notFound();

  const projDir = path.join(PROJECT_ROOT, "projects", slug);
  const shotLists = await fs
    .readdir(projDir)
    .then((entries) =>
      entries.filter((e) => e.toLowerCase().startsWith("shot-list") && e.endsWith(".md")),
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
        <h1 className="capitalize">Timeline editor — {project.name}</h1>
        <p className="text-[var(--color-mist)] mt-2">
          Visual shot list. Click any shot to see its details. Hero-color-coded
          by act. Placeholders render as black in the final cut.
        </p>
      </div>

      <EditorShell project={slug} shotLists={shotLists} />
    </div>
  );
}
