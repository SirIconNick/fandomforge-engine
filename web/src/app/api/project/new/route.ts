import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

interface NewProjectRequest {
  slug: string;
  theme: string;
  fandoms: Array<{ name: string; share?: number; primary_characters?: string[] }>;
  vibe: string;
  length_seconds: number;
  platform_target: string;
  song?: { title: string; artist: string };
  resolution?: { width: number; height: number };
  fps?: number;
  target_lufs?: number;
}

function sanitizeSlug(s: string): string {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9-_]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

export async function POST(req: NextRequest) {
  let body: NewProjectRequest;
  try {
    body = (await req.json()) as NewProjectRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const slug = sanitizeSlug(body.slug ?? "");
  if (!slug) {
    return NextResponse.json({ error: "slug is required and must be non-empty after sanitization" }, { status: 400 });
  }

  const requiredFields = ["theme", "fandoms", "vibe", "length_seconds", "platform_target"];
  const indexed = body as unknown as Record<string, unknown>;
  for (const f of requiredFields) {
    if (indexed[f] === undefined || indexed[f] === null) {
      return NextResponse.json({ error: `Missing required field '${f}'` }, { status: 400 });
    }
  }

  const projectPath = path.join(PROJECT_ROOT, "projects", slug);
  try {
    await fs.access(projectPath);
    return NextResponse.json({ error: `Project '${slug}' already exists` }, { status: 409 });
  } catch {
    // does not exist — good
  }

  await fs.mkdir(path.join(projectPath, "raw"), { recursive: true });
  await fs.mkdir(path.join(projectPath, "derived"), { recursive: true });
  await fs.mkdir(path.join(projectPath, "data"), { recursive: true });
  await fs.mkdir(path.join(projectPath, "exports"), { recursive: true });

  const editPlan = {
    schema_version: 1,
    project_slug: slug,
    concept: {
      theme: body.theme,
      one_sentence: body.theme,
    },
    song: body.song ?? { title: "", artist: "" },
    fandoms: body.fandoms,
    vibe: body.vibe,
    length_seconds: body.length_seconds,
    platform_target: body.platform_target,
    resolution: body.resolution ?? { width: 1920, height: 1080 },
    fps: body.fps ?? 24,
    target_lufs: body.target_lufs ?? platformTargetLufs(body.platform_target),
    acts: [
      {
        number: 1,
        name: "Setup",
        start_sec: 0,
        end_sec: Math.max(1, Math.floor(body.length_seconds / 4)),
        energy_target: 30,
        emotional_goal: "establish the through-line",
      },
      {
        number: 2,
        name: "Descent",
        start_sec: Math.max(1, Math.floor(body.length_seconds / 4)),
        end_sec: Math.max(2, Math.floor(body.length_seconds / 2)),
        energy_target: 60,
        emotional_goal: "raise stakes",
      },
      {
        number: 3,
        name: "Peak",
        start_sec: Math.max(2, Math.floor(body.length_seconds / 2)),
        end_sec: Math.max(3, Math.floor((body.length_seconds * 3) / 4)),
        energy_target: 90,
        emotional_goal: "emotional or kinetic peak",
      },
      {
        number: 4,
        name: "Release",
        start_sec: Math.max(3, Math.floor((body.length_seconds * 3) / 4)),
        end_sec: body.length_seconds,
        energy_target: 50,
        emotional_goal: "landing and reflection",
      },
    ],
  };

  await fs.writeFile(
    path.join(projectPath, "data", "edit-plan.json"),
    JSON.stringify(editPlan, null, 2),
    "utf8"
  );

  const projectConfig = {
    schema_version: 1,
    character: slug.split("-")[0] ?? slug,
    character_aliases: [],
    song: "",
    song_offset_sec: 0.0,
    song_gain_db: -6.0,
    default_duck_db: -10.0,
    template: "HauntedVeteran",
    vision_context: body.vibe,
    export_presets: [body.platform_target],
    lut_name: "cinematic-teal-orange",
    lut_intensity: 0.5,
    platform_target: body.platform_target,
    target_loudness_lufs: body.target_lufs ?? platformTargetLufs(body.platform_target),
    true_peak_ceiling_dbtp: -1,
  };
  await fs.writeFile(
    path.join(projectPath, "project-config.json"),
    JSON.stringify(projectConfig, null, 2),
    "utf8"
  );

  return NextResponse.json({
    ok: true,
    slug,
    path: projectPath,
    editPlanPath: path.join(projectPath, "data", "edit-plan.json"),
  });
}

function platformTargetLufs(platform: string): number {
  switch (platform) {
    case "youtube":
      return -14;
    case "tiktok":
    case "reels":
    case "shorts":
      return -14;
    case "twitter":
      return -14;
    default:
      return -16;
  }
}
