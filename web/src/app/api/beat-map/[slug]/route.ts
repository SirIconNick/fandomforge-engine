import { NextResponse } from "next/server";
import { loadBeatMap } from "@/lib/fs";

type Params = Promise<{ slug: string }>;

export async function GET(_req: Request, { params }: { params: Params }) {
  const { slug } = await params;
  const data = await loadBeatMap(slug);
  if (!data) {
    return NextResponse.json({ error: "Beat map not found" }, { status: 404 });
  }
  return NextResponse.json(data);
}
