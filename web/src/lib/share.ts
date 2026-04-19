import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

export interface ShareConfig {
  schema_version: number;
  project_slug: string;
  token: string;
  created_at: string;
  note?: string;
}

export async function lookupShareToken(token: string): Promise<ShareConfig | null> {
  if (!token || token.length < 16) return null;
  const projectsDir = path.join(PROJECT_ROOT, "projects");
  try {
    const entries = await fs.readdir(projectsDir);
    for (const entry of entries) {
      if (entry.startsWith(".")) continue;
      const sharePath = path.join(projectsDir, entry, "share.json");
      try {
        const raw = await fs.readFile(sharePath, "utf8");
        const data = JSON.parse(raw) as ShareConfig;
        if (data.token === token) return data;
      } catch {
        /* missing share.json or parse error — skip */
      }
    }
  } catch {
    return null;
  }
  return null;
}
