import { NextResponse } from "next/server";

/**
 * GET /api/env
 * Returns which optional environment features are enabled. Never returns the
 * key values themselves — only booleans.
 */
export async function GET() {
  return NextResponse.json({
    has_anthropic_key: Boolean(process.env.ANTHROPIC_API_KEY),
    has_openai_key: Boolean(process.env.OPENAI_API_KEY),
    has_jina_key: Boolean(process.env.JINA_API_KEY),
    anthropic_model: process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-6",
  });
}
