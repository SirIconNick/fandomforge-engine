import { loadProjects, loadBeatMap } from "@/lib/fs";
import { BeatMapVisualizer } from "@/components/BeatMapVisualizer";
import Link from "next/link";

export default async function BeatMapPage() {
  const projects = await loadProjects();
  const withMaps = projects.filter((p) => p.hasBeatMap);
  const first = withMaps[0];
  const beatMap = first ? await loadBeatMap(first.slug) : null;

  return (
    <div className="space-y-10">
      <header className="space-y-4">
        <h1>Beat Mapper</h1>
        <p className="text-xl text-[var(--color-mist)] max-w-2xl">
          Visualize the timing structure of your song — beats, downbeats, drops,
          buildups, breakdowns, and the energy curve.
        </p>
      </header>

      <section>
        <h2 className="mb-4">How to generate a beat map</h2>
        <div className="space-y-3 font-mono text-sm bg-white/[0.02] border border-white/10 rounded p-5">
          <div>
            <span className="text-[var(--color-ash)]"># Activate the Python env</span>
            <div>source .venv/bin/activate</div>
          </div>
          <div>
            <span className="text-[var(--color-ash)]"># Analyze your song</span>
            <div>
              ff beat analyze <span className="text-[var(--color-ember)]">path/to/song.mp3</span>{" "}
              -o <span className="text-[var(--color-ember)]">projects/my-edit/beat-map.json</span>
            </div>
          </div>
          <div>
            <span className="text-[var(--color-ash)]"># Or with a tempo hint</span>
            <div>
              ff beat analyze song.mp3 --tempo-hint{" "}
              <span className="text-[var(--color-ember)]">140</span> -o beat-map.json
            </div>
          </div>
        </div>
      </section>

      {withMaps.length === 0 ? (
        <section>
          <div className="p-8 border border-dashed border-white/10 rounded text-center text-[var(--color-mist)]">
            No beat maps generated yet. Run the command above, then refresh.
          </div>
        </section>
      ) : (
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2>Projects with beat maps</h2>
            <div className="flex gap-2 text-sm">
              {withMaps.map((p) => (
                <Link
                  key={p.slug}
                  href={`/projects/${p.slug}`}
                  className="px-3 py-1 border border-white/10 rounded hover:border-[var(--color-forge)]/50 transition-colors"
                >
                  {p.name}
                </Link>
              ))}
            </div>
          </div>
          {first && beatMap && (
            <div className="space-y-2">
              <div className="text-sm text-[var(--color-ash)]">
                Showing: <Link className="text-[var(--color-forge)] hover:underline" href={`/projects/${first.slug}`}>{first.name}</Link>
              </div>
              <BeatMapVisualizer data={beatMap} />
            </div>
          )}
        </section>
      )}
    </div>
  );
}
