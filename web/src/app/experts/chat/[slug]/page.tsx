import { loadExperts } from "@/lib/fs";
import { notFound } from "next/navigation";
import ExpertChat from "./ExpertChat";

export default async function ExpertChatPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ project?: string }>;
}) {
  const { slug } = await params;
  const { project } = await searchParams;
  const experts = await loadExperts();
  const expert = experts.find((e) => e.slug === slug);
  if (!expert) notFound();

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1>Chat with {expert.name}</h1>
        <p className="text-sm text-white/60">{expert.description}</p>
        {project && (
          <p className="text-xs text-white/50">
            grounded in project <code className="font-mono bg-white/5 px-1 rounded">{project}</code>
          </p>
        )}
      </header>
      <ExpertChat expertSlug={slug} projectSlug={project} />
    </div>
  );
}
