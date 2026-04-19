import path from "node:path";
import { promises as fs } from "node:fs";
import { notFound } from "next/navigation";
import { PROJECT_ROOT } from "@/lib/fs";
import QAPanel from "./QAPanel";

async function loadQAReport(slug: string): Promise<unknown | null> {
  const p = path.join(PROJECT_ROOT, "projects", slug, "data", "qa-report.json");
  try {
    return JSON.parse(await fs.readFile(p, "utf8"));
  } catch {
    return null;
  }
}

export default async function QAPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const projectPath = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projectPath);
  } catch {
    notFound();
  }

  const initialReport = await loadQAReport(slug);
  return <QAPanel slug={slug} initialReport={initialReport as unknown} />;
}
