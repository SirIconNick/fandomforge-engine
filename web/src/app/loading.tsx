export default function Loading() {
  return (
    <div className="max-w-5xl mx-auto px-6 py-16 space-y-4">
      <div className="animate-pulse space-y-3">
        <div className="h-6 bg-white/10 rounded w-1/3" />
        <div className="h-4 bg-white/5 rounded w-2/3" />
        <div className="h-4 bg-white/5 rounded w-1/2" />
      </div>
      <p className="text-sm text-white/50 mt-8">Loading…</p>
    </div>
  );
}
