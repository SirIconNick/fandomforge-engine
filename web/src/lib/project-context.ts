import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT, fileExists } from "@/lib/fs";
import { sha256, atomicWriteJson, atomicWriteString } from "@/lib/atomic-write";
import type { ArtifactType } from "@/lib/schemas";

export interface ArtifactSnapshot<T = unknown> {
  artifact: ArtifactType;
  path: string;
  exists: boolean;
  data: T | null;
  raw: string | null;
  sha256: string | null;
}

const JSON_ARTIFACTS: ArtifactType[] = [
  "edit-plan",
  "beat-map",
  "shot-list",
  "color-plan",
  "transition-plan",
  "audio-plan",
  "title-plan",
  "qa-report",
  "fandoms",
];

function artifactFilename(artifact: ArtifactType): string {
  if (artifact === "fandoms") return "fandoms.json";
  return `${artifact}.json`;
}

export function projectRoot(slug: string): string {
  return path.join(PROJECT_ROOT, "projects", slug);
}

export function artifactPath(slug: string, artifact: ArtifactType): string {
  if (artifact === "fandoms") {
    return path.join(projectRoot(slug), "fandoms.json");
  }
  return path.join(projectRoot(slug), "data", artifactFilename(artifact));
}

export function historyPath(slug: string, artifact: ArtifactType): string {
  return path.join(projectRoot(slug), ".history", `${artifact}.jsonl`);
}

export async function readArtifact<T = unknown>(
  slug: string,
  artifact: ArtifactType
): Promise<ArtifactSnapshot<T>> {
  const filePath = artifactPath(slug, artifact);
  const exists = await fileExists(filePath);
  if (!exists) {
    return { artifact, path: filePath, exists: false, data: null, raw: null, sha256: null };
  }
  const raw = await fs.readFile(filePath, "utf8");
  let data: T | null = null;
  try {
    data = JSON.parse(raw) as T;
  } catch {
    data = null;
  }
  return { artifact, path: filePath, exists: true, data, raw, sha256: sha256(raw) };
}

export async function writeArtifactAtomic(
  slug: string,
  artifact: ArtifactType,
  data: unknown
): Promise<ArtifactSnapshot> {
  const filePath = artifactPath(slug, artifact);
  await atomicWriteJson(filePath, data);
  const raw = await fs.readFile(filePath, "utf8");
  return {
    artifact,
    path: filePath,
    exists: true,
    data,
    raw,
    sha256: sha256(raw),
  };
}

export async function loadProjectArtifacts(
  slug: string
): Promise<Record<ArtifactType, ArtifactSnapshot>> {
  const entries = await Promise.all(
    JSON_ARTIFACTS.map(async (a) => [a, await readArtifact(slug, a)] as const)
  );
  return Object.fromEntries(entries) as Record<ArtifactType, ArtifactSnapshot>;
}

export function renderProjectContextMarkdown(
  slug: string,
  artifacts: Record<ArtifactType, ArtifactSnapshot>
): string {
  const parts: string[] = [`# Current project: ${slug}`];
  for (const artifact of JSON_ARTIFACTS) {
    const snap = artifacts[artifact];
    if (!snap?.exists || !snap.raw) continue;
    const filename = artifactFilename(artifact);
    parts.push(`## ${filename}\n\`\`\`json\n${snap.raw.trim()}\n\`\`\``);
  }
  return parts.join("\n\n");
}

export async function projectExists(slug: string): Promise<boolean> {
  return fileExists(projectRoot(slug));
}

export { JSON_ARTIFACTS };

export async function ensureProjectDirs(slug: string): Promise<void> {
  const root = projectRoot(slug);
  await fs.mkdir(path.join(root, "data"), { recursive: true });
  await fs.mkdir(path.join(root, ".history"), { recursive: true });
}

export async function touchMarker(slug: string, marker: string): Promise<void> {
  const p = path.join(projectRoot(slug), ".history", marker);
  await atomicWriteString(p, new Date().toISOString() + "\n");
}
