import Link from "next/link";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";

const DEMO_SLUG = "demo-project";

interface FixtureItem {
  id: string;
  url: string;
  kind: string;
  license: string;
  filename: string;
  expected_duration_sec: number;
  tier: string;
}

async function loadManifest(): Promise<{ items: FixtureItem[] }> {
  const p = path.join(PROJECT_ROOT, "tools", "tests", "fixtures", "manifest.json");
  try {
    const raw = await fs.readFile(p, "utf8");
    return JSON.parse(raw);
  } catch {
    return { items: [] };
  }
}

async function cacheState(items: FixtureItem[]): Promise<Record<string, boolean>> {
  const mediaDir = path.join(PROJECT_ROOT, "tools", "tests", "fixtures", "media");
  const out: Record<string, boolean> = {};
  for (const item of items) {
    try {
      const stat = await fs.stat(path.join(mediaDir, item.filename));
      out[item.id] = stat.size > 0;
    } catch {
      out[item.id] = false;
    }
  }
  return out;
}

export default async function OnboardingDemoPage() {
  const manifest = await loadManifest();
  const cache = await cacheState(manifest.items);
  const allCached =
    manifest.items.length > 0 && manifest.items.every((i) => cache[i.id]);
  const nothingYet = manifest.items.every((i) => !cache[i.id]);

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1>Demo project</h1>
        <p className="text-sm text-white/60 max-w-2xl">
          Spin up a full FandomForge project using the curated legal-test-media
          library. Every asset is either CC0, CC-licensed, or public domain — no
          copyright risk, no waiting for a song to be picked, no hunting for source
          clips. One click and you see the whole pipeline working end to end.
        </p>
      </div>

      <section className="space-y-3">
        <h2>1. Cache the legal fixtures</h2>
        <p className="text-sm text-white/70">
          FandomForge ships a manifest of legally-usable media (Pexels CC0 video,
          Internet Archive public domain, Creative Commons music from Incompetech).
          The files aren&apos;t checked into git — they&apos;re fetched on demand.
        </p>
        <div className="bg-black/40 border border-white/10 rounded p-3 text-xs font-mono">
          <div className="text-white/50"># from the project root</div>
          <div>ff fixtures fetch</div>
        </div>
        <div className="text-sm">
          Manifest status:{" "}
          {allCached ? (
            <span className="text-green-300">all {manifest.items.length} fixtures cached</span>
          ) : nothingYet ? (
            <span className="text-yellow-300">
              nothing cached yet — run the command above
            </span>
          ) : (
            <span className="text-yellow-300">
              partial cache (
              {manifest.items.filter((i) => cache[i.id]).length} /{" "}
              {manifest.items.length})
            </span>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-white/50 uppercase tracking-wide">
                <th className="pb-1">id</th>
                <th className="pb-1">kind</th>
                <th className="pb-1">license</th>
                <th className="pb-1">cached</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {manifest.items.map((item) => (
                <tr key={item.id} className="border-t border-white/10">
                  <td className="py-1.5">{item.id}</td>
                  <td>{item.kind}</td>
                  <td className="text-white/60">{item.license}</td>
                  <td>
                    {cache[item.id] ? (
                      <span className="text-green-300">yes</span>
                    ) : (
                      <span className="text-white/40">no</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-3">
        <h2>2. Scaffold the demo project</h2>
        <p className="text-sm text-white/70">
          Create a new project called <code className="font-mono text-xs">{DEMO_SLUG}</code>,
          then copy the cached audio/video into the right places.
        </p>
        <div className="bg-black/40 border border-white/10 rounded p-3 text-xs font-mono space-y-1">
          <div>ff project new {DEMO_SLUG}</div>
          <div>
            cp tools/tests/fixtures/media/incompetech-sneaky-snitch.mp3 projects/
            {DEMO_SLUG}/assets/song.mp3
          </div>
          <div>
            cp tools/tests/fixtures/media/pexels-*.mp4 projects/{DEMO_SLUG}/raw/
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <h2>3. Run the pipeline</h2>
        <p className="text-sm text-white/70">
          Once the demo project has media in place, run the pipeline or the
          auto-pilot to see everything work end to end.
        </p>
        <div className="flex gap-2 flex-wrap">
          <Link
            href={`/projects/${DEMO_SLUG}`}
            className="px-4 py-2 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-sm"
          >
            Open demo project →
          </Link>
          <Link
            href={`/projects/${DEMO_SLUG}/autopilot`}
            className="px-4 py-2 rounded border border-white/20 text-sm"
          >
            Run auto-pilot (when available)
          </Link>
        </div>
      </section>

      <section className="space-y-2 text-xs text-white/50">
        <h3 className="text-sm text-white/70">Why this matters</h3>
        <p>
          Testing a video pipeline without real media gets you into a trap — you
          mock everything, the unit tests pass, and the first time a real song
          hits the beat detector you find three edge cases in an afternoon. The
          fixture library prevents that. Each fixture has a documented license;
          every integration test runs on media you can legally use for testing
          and demo purposes.
        </p>
      </section>
    </div>
  );
}
