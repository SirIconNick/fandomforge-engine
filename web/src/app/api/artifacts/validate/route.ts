import { NextRequest, NextResponse } from "next/server";
import { validateArtifact, type ArtifactType } from "@/lib/schemas";

interface ValidateRequest {
  artifact: ArtifactType;
  data: unknown;
}

export async function POST(req: NextRequest) {
  let body: ValidateRequest;
  try {
    body = (await req.json()) as ValidateRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }
  if (!body.artifact) {
    return NextResponse.json(
      { error: "artifact field required" },
      { status: 400 }
    );
  }
  const result = await validateArtifact(body.artifact, body.data);
  return NextResponse.json({
    ok: result.ok,
    errors: result.errors,
  });
}
