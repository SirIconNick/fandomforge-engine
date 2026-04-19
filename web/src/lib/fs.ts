import { promises as fs } from "node:fs";
import path from "node:path";
import matter from "gray-matter";

/**
 * Root of the project workspace. Resolved from CWD of the Next dev server,
 * which is assumed to be the `web/` subdirectory.
 */
export const PROJECT_ROOT = path.resolve(process.cwd(), "..");

export async function fileExists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

export async function readMarkdown(p: string): Promise<{
  content: string;
  data: Record<string, unknown>;
}> {
  const raw = await fs.readFile(p, "utf8");
  const parsed = matter(raw);
  return { content: parsed.content, data: parsed.data };
}

export async function listDir(p: string): Promise<string[]> {
  try {
    return await fs.readdir(p);
  } catch {
    return [];
  }
}

export interface ExpertAgent {
  slug: string;
  name: string;
  description: string;
  color: string;
  model: string;
  content: string;
}

export async function loadExperts(): Promise<ExpertAgent[]> {
  const agentsDir = path.join(PROJECT_ROOT, "agents");
  const entries = await listDir(agentsDir);
  const experts: ExpertAgent[] = [];
  for (const entry of entries) {
    if (!entry.endsWith(".md") || entry === "README.md") continue;
    const fullPath = path.join(agentsDir, entry);
    const { content, data } = await readMarkdown(fullPath);
    experts.push({
      slug: entry.replace(/\.md$/, ""),
      name: (data.name as string) ?? entry.replace(/\.md$/, ""),
      description: (data.description as string) ?? "",
      color: (data.color as string) ?? "white",
      model: (data.model as string) ?? "",
      content,
    });
  }
  return experts;
}

export interface KnowledgeDoc {
  slug: string;
  title: string;
  content: string;
}

function extractTitleFromMarkdown(md: string): string {
  const match = md.match(/^#\s+(.+)$/m);
  return match?.[1]?.trim() ?? "Untitled";
}

export async function loadKnowledgeDocs(): Promise<KnowledgeDoc[]> {
  const dir = path.join(PROJECT_ROOT, "docs", "knowledge");
  const entries = await listDir(dir);
  const docs: KnowledgeDoc[] = [];
  for (const entry of entries) {
    if (!entry.endsWith(".md") || entry === "README.md") continue;
    const fullPath = path.join(dir, entry);
    const raw = await fs.readFile(fullPath, "utf8");
    docs.push({
      slug: entry.replace(/\.md$/, ""),
      title: extractTitleFromMarkdown(raw),
      content: raw,
    });
  }
  return docs.sort((a, b) => a.title.localeCompare(b.title));
}

export interface ProjectSummary {
  slug: string;
  name: string;
  theme: string | null;
  hasBeatMap: boolean;
  hasEditPlan: boolean;
  hasShotList: boolean;
  updatedAt: string;
}

export async function loadProjects(): Promise<ProjectSummary[]> {
  const dir = path.join(PROJECT_ROOT, "projects");
  const entries = await listDir(dir);
  const projects: ProjectSummary[] = [];
  for (const slug of entries) {
    if (slug.startsWith(".") || slug.startsWith("_")) continue;
    const projPath = path.join(dir, slug);
    const stat = await fs.stat(projPath).catch(() => null);
    if (!stat?.isDirectory()) continue;
    // Check both new (plans/) and legacy (root) layouts
    const editPlanCandidates = [
      path.join(projPath, "plans", "edit-plan.md"),
      path.join(projPath, "edit-plan.md"),
    ];
    const beatMapCandidates = [
      path.join(projPath, "data", "beat-map.json"),
      path.join(projPath, "beat-map.json"),
    ];
    const shotListCandidates = [
      path.join(projPath, "plans", "shot-list.md"),
      path.join(projPath, "shot-list.md"),
    ];

    const editPlanPath = (
      await Promise.all(editPlanCandidates.map((p) => fileExists(p)))
    ).findIndex(Boolean);
    const resolvedEditPlan =
      editPlanPath >= 0 ? editPlanCandidates[editPlanPath] : null;

    const hasBeatMap = (
      await Promise.all(beatMapCandidates.map((p) => fileExists(p)))
    ).some(Boolean);
    const hasShotList = (
      await Promise.all(shotListCandidates.map((p) => fileExists(p)))
    ).some(Boolean);

    let theme: string | null = null;
    if (resolvedEditPlan) {
      const raw = await fs.readFile(resolvedEditPlan, "utf8");
      const themeMatch = raw.match(/THEME\s*(?:\n|║\s*)?\s*(?:║\s*)?([^║\n].+?)(?:\s*║|\n)/i);
      if (themeMatch?.[1]) {
        theme = themeMatch[1].trim();
      }
    }

    projects.push({
      slug,
      name: slug.replace(/[-_]/g, " "),
      theme,
      hasBeatMap,
      hasEditPlan: !!resolvedEditPlan,
      hasShotList,
      updatedAt: stat.mtime.toISOString(),
    });
  }
  return projects.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export interface BeatMapData {
  song: string;
  artist: string;
  duration_sec: number;
  bpm: number;
  bpm_confidence: number;
  time_signature: string;
  beats: number[];
  downbeats: number[];
  onsets: number[];
  drops?: Array<{ time: number; intensity: number; type: string }>;
  buildups?: Array<{ start: number; end: number; curve: string }>;
  breakdowns?: Array<{ start: number; end: number; intensity: number }>;
  energy_curve?: Array<[number, number]>;
}

export async function loadBeatMap(projectSlug: string): Promise<BeatMapData | null> {
  const base = path.join(PROJECT_ROOT, "projects", projectSlug);
  const candidates = [
    path.join(base, "data", "beat-map.json"),
    path.join(base, "beat-map.json"),
  ];
  for (const p of candidates) {
    if (await fileExists(p)) {
      const raw = await fs.readFile(p, "utf8");
      return JSON.parse(raw) as BeatMapData;
    }
  }
  return null;
}
