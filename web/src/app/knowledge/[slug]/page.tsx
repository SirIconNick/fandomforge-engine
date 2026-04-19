import Link from "next/link";
import { notFound } from "next/navigation";
import { loadKnowledgeDocs } from "@/lib/fs";
import { MarkdownViewer } from "@/components/MarkdownViewer";

export async function generateStaticParams() {
  const docs = await loadKnowledgeDocs();
  return docs.map((d) => ({ slug: d.slug }));
}

type Params = Promise<{ slug: string }>;

export default async function KnowledgeDocPage({ params }: { params: Params }) {
  const { slug } = await params;
  const docs = await loadKnowledgeDocs();
  const doc = docs.find((d) => d.slug === slug);
  if (!doc) notFound();

  return (
    <div className="space-y-6">
      <Link
        href="/knowledge"
        className="inline-block text-sm text-[var(--color-forge)] hover:underline"
      >
        ← Knowledge base
      </Link>
      <MarkdownViewer content={doc.content} />
    </div>
  );
}
