import { NextResponse } from "next/server";
import { startPipelineRun, listRuns } from "@/lib/runner";

export async function POST(req: Request) {
  const body = await req.json();
  if (!body?.project) {
    return NextResponse.json(
      { error: "Missing 'project' in request body" },
      { status: 400 },
    );
  }
  const run = startPipelineRun(body);
  return NextResponse.json({ id: run.id, run });
}

export async function GET() {
  return NextResponse.json({ runs: listRuns() });
}
