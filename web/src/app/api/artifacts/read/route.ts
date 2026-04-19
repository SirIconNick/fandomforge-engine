import { NextRequest, NextResponse } from "next/server";
import { readArtifact, projectExists } from "@/lib/project-context";
import type { ArtifactType } from "@/lib/schemas";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const projectSlug = url.searchParams.get("project");
  const artifact = url.searchParams.get("artifact") as ArtifactType | null;

  if (!projectSlug || !artifact) {
    return NextResponse.json(
      { error: "Missing query params: project, artifact" },
      { status: 400 }
    );
  }

  if (!(await projectExists(projectSlug))) {
    return NextResponse.json(
      { error: `Project not found: ${projectSlug}` },
      { status: 404 }
    );
  }

  const snapshot = await readArtifact(projectSlug, artifact);
  return NextResponse.json({
    ok: true,
    artifact: snapshot.artifact,
    project_slug: projectSlug,
    exists: snapshot.exists,
    data: snapshot.data,
    sha256: snapshot.sha256,
  });
}
