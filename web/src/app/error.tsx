"use client";

import Link from "next/link";

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="max-w-3xl mx-auto px-6 py-16 space-y-4">
      <h1>Something broke</h1>
      <p className="text-sm text-white/70">
        An unhandled error bubbled up to the app root. The full message is
        below; the details live in the server logs for the engineers.
      </p>
      <pre className="bg-black/40 border border-red-500/30 rounded p-4 text-xs text-red-200 whitespace-pre-wrap overflow-auto">
        {error.message}
        {error.digest && `\n\ndigest: ${error.digest}`}
      </pre>
      <div className="flex gap-3">
        <button
          onClick={reset}
          className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-sm"
        >
          Try again
        </button>
        <Link
          href="/"
          className="px-4 py-2 rounded border border-white/10 text-sm"
        >
          Go home
        </Link>
      </div>
    </div>
  );
}
