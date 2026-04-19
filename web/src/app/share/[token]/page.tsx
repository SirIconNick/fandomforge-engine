import { notFound } from "next/navigation";
import { lookupShareToken } from "@/lib/share";
import { loadProjectArtifacts, renderProjectContextMarkdown } from "@/lib/project-context";
import { MarkdownViewer } from "@/components/MarkdownViewer";

type Params = Promise<{ token: string }>;

export default async function SharePage({ params }: { params: Params }) {
  const { token } = await params;
  const share = await lookupShareToken(token);
  if (!share) notFound();

  const artifacts = await loadProjectArtifacts(share.project_slug);
  const markdown = renderProjectContextMarkdown(share.project_slug, artifacts);

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="border border-[var(--color-forge)]/30 bg-[var(--color-forge)]/5 rounded p-3 text-xs">
        <div className="font-semibold text-[var(--color-forge)]">Shared read-only view</div>
        <div className="text-white/60 mt-1">
          You are viewing a read-only snapshot of the project{" "}
          <code className="font-mono">{share.project_slug}</code>. Nothing here is editable —
          to make changes, the owner needs to share the project folder directly.
        </div>
        {share.note && (
          <div className="text-white/70 mt-2 italic">Note: {share.note}</div>
        )}
      </div>

      <h1 className="capitalize">{share.project_slug.replace(/[-_]/g, " ")}</h1>

      <div className="border border-white/10 rounded p-6 prose prose-invert max-w-none">
        <MarkdownViewer content={markdown} />
      </div>

      <footer className="text-[10px] text-white/40">
        Shared at {share.created_at.replace("T", " ").slice(0, 19)}
      </footer>
    </div>
  );
}
