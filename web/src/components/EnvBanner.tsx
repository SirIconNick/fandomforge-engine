/**
 * Warn the user when ANTHROPIC_API_KEY is absent. The LLM-backed experts
 * silently degrade to heuristic mode when the key is missing; this banner
 * surfaces that fact so nobody wonders why the edit-strategist feels thin.
 */
export function EnvBanner() {
  const hasAnthropic = Boolean(process.env.ANTHROPIC_API_KEY);
  if (hasAnthropic) return null;

  return (
    <div className="border border-yellow-500/40 bg-yellow-500/5 rounded p-4 text-sm">
      <div className="font-semibold text-yellow-200 mb-1">
        No Anthropic API key detected
      </div>
      <div className="text-yellow-100/80">
        The edit-strategist and expert chat fall back to heuristic output
        without an API key. Set{" "}
        <code className="bg-black/40 px-1 rounded">ANTHROPIC_API_KEY</code> in
        <code className="bg-black/40 px-1 rounded">web/.env.local</code> and
        restart the dev server to enable real LLM responses.
      </div>
    </div>
  );
}
