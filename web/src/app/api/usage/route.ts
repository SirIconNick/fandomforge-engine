import { NextResponse } from "next/server";
import { readChatUsage, summarize } from "@/lib/chat-usage";

export async function GET() {
  const entries = await readChatUsage();
  const stats = summarize(entries);
  const recent = entries.slice(-50).reverse();
  return NextResponse.json({ ok: true, stats, recent });
}
