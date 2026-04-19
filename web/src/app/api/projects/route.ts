import { NextResponse } from "next/server";
import { loadProjects } from "@/lib/fs";

export const dynamic = "force-dynamic";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
} as const;

export function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

export async function GET() {
  try {
    const projects = await loadProjects();
    return NextResponse.json(
      {
        projects: projects.map((p) => ({
          slug: p.slug,
          name: p.name,
          theme: p.theme,
          has_beat_map: p.hasBeatMap,
          has_edit_plan: p.hasEditPlan,
          has_shot_list: p.hasShotList,
          updated_at: p.updatedAt,
        })),
      },
      { headers: CORS_HEADERS }
    );
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to list projects", detail: (error as Error).message },
      { status: 500, headers: CORS_HEADERS }
    );
  }
}
