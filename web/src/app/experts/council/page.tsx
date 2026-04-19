import { loadExperts } from "@/lib/fs";
import CouncilView from "@/components/CouncilView";

interface SearchParams {
  project?: string;
}

export default async function CouncilPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const { project } = await searchParams;
  const experts = await loadExperts();
  const lite = experts
    .map((e) => ({ slug: e.slug, name: e.name, color: e.color }))
    .sort((a, b) => a.slug.localeCompare(b.slug));

  return (
    <div className="space-y-6">
      <div>
        <h1>Expert council</h1>
        <p className="text-sm text-white/60 max-w-2xl">
          Ask the same question to 2–4 experts in parallel. Each answers from their own
          specialty, with their own proposed patches. Conflicts between proposals are
          highlighted so you can pick one or combine them manually.
        </p>
      </div>
      <CouncilView experts={lite} initialProject={project} />
    </div>
  );
}
