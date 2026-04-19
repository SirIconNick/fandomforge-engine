#!/usr/bin/env bash
# Verify the Anthropic API key in web/.env.local actually works.
# Makes one small request (~40 tokens) and prints the response + usage.
# Run from the repo root: scripts/verify-anthropic.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f "web/.env.local" ]; then
  echo "web/.env.local not found. Copy .env.local.example and add ANTHROPIC_API_KEY."
  exit 1
fi

cat > web/__verify.mjs << 'JSFILE'
import Anthropic from "@anthropic-ai/sdk";
import fs from "node:fs";

const env = fs.readFileSync("./.env.local", "utf8");
const match = env.match(/^ANTHROPIC_API_KEY=(.+)$/m);
if (!match) { console.error("ANTHROPIC_API_KEY not found in .env.local"); process.exit(1); }

const client = new Anthropic({ apiKey: match[1].trim() });

try {
  const res = await client.messages.create({
    model: process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-5-20250929",
    max_tokens: 64,
    system: [{
      type: "text",
      text: "You are a FandomForge health-check. Reply with exactly: API LIVE.",
      cache_control: { type: "ephemeral" },
    }],
    messages: [{ role: "user", content: "check" }],
  });
  const text = res.content.filter(b => b.type === "text").map(b => b.text).join("");
  console.log("reply :", JSON.stringify(text.trim()));
  console.log("model :", res.model);
  console.log("stop  :", res.stop_reason);
  console.log("usage :", JSON.stringify(res.usage));
  console.log("\nOK — Anthropic API is live and your key works.");
} catch (err) {
  console.error("FAIL:", err.message ?? err);
  if (err.status === 400 && String(err.message ?? "").includes("credit balance")) {
    console.error("\nYour account needs a billing top-up at https://console.anthropic.com/settings/billing");
  }
  process.exit(1);
}
JSFILE

cd web
node __verify.mjs
STATUS=$?
rm -f __verify.mjs
exit $STATUS
