"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type Platform = "youtube" | "tiktok" | "reels" | "shorts" | "twitter" | "master";
type Vibe =
  | "action"
  | "emotional"
  | "hype"
  | "sad"
  | "funny"
  | "mixed"
  | "cinematic"
  | "horror"
  | "romantic"
  | "triumphant";

const PLATFORM_PRESETS: Record<Platform, { width: number; height: number; fps: number; lufs: number }> = {
  youtube: { width: 1920, height: 1080, fps: 24, lufs: -14 },
  tiktok: { width: 1080, height: 1920, fps: 30, lufs: -14 },
  reels: { width: 1080, height: 1920, fps: 30, lufs: -14 },
  shorts: { width: 1080, height: 1920, fps: 30, lufs: -14 },
  twitter: { width: 1920, height: 1080, fps: 30, lufs: -14 },
  master: { width: 1920, height: 1080, fps: 24, lufs: -16 },
};

export default function NewProjectPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [slug, setSlug] = useState("");
  const [theme, setTheme] = useState("");
  const [songTitle, setSongTitle] = useState("");
  const [songArtist, setSongArtist] = useState("");
  const [fandomsText, setFandomsText] = useState("Marvel\nStar Wars\nHarry Potter");
  const [vibe, setVibe] = useState<Vibe>("emotional");
  const [length, setLength] = useState(120);
  const [platform, setPlatform] = useState<Platform>("youtube");
  const [status, setStatus] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  function nextStep() {
    setStep((s) => Math.min(5, s + 1));
  }
  function prevStep() {
    setStep((s) => Math.max(1, s - 1));
  }

  async function submit() {
    setSubmitting(true);
    setStatus("creating project...");
    const fandoms = fandomsText
      .split(/\n+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((name) => ({ name }));
    const preset = PLATFORM_PRESETS[platform];
    const res = await fetch("/api/project/new", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        slug,
        theme,
        fandoms,
        vibe,
        length_seconds: length,
        platform_target: platform,
        song: { title: songTitle, artist: songArtist },
        resolution: { width: preset.width, height: preset.height },
        fps: preset.fps,
        target_lufs: preset.lufs,
      }),
    });
    const body = (await res.json()) as { ok?: boolean; error?: string; slug?: string };
    if (!res.ok || !body.ok) {
      setStatus(`error: ${body.error ?? res.statusText}`);
      setSubmitting(false);
      return;
    }
    setStatus("created — redirecting to upload");
    router.push(`/projects/${body.slug}#upload`);
  }

  return (
    <div className="max-w-2xl mx-auto py-8 px-4 space-y-6">
      <header>
        <h1 className="text-2xl font-serif mb-1">New project</h1>
        <p className="text-sm text-white/60">Step {step} of 5</p>
      </header>

      <div className="h-2 bg-white/5 rounded overflow-hidden">
        <div
          className="h-full bg-[var(--color-accent,#ff5a1f)] transition-all"
          style={{ width: `${(step / 5) * 100}%` }}
        />
      </div>

      {step === 1 && (
        <section className="space-y-4">
          <div>
            <label className="block text-xs uppercase tracking-wide text-white/60 mb-1">Project slug</label>
            <input
              className="w-full bg-black/30 border border-white/10 rounded px-3 py-2"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="mentor-loss-multifandom"
            />
            <p className="text-xs text-white/50 mt-1">
              Lowercase letters, numbers, hyphens. This becomes the folder name.
            </p>
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wide text-white/60 mb-1">Theme (one sentence)</label>
            <textarea
              className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 min-h-24"
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              placeholder="Every mentor who saw the fall coming and stayed anyway."
            />
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="space-y-4">
          <h2 className="text-lg font-serif">Song</h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs uppercase tracking-wide text-white/60 mb-1">Title</label>
              <input
                className="w-full bg-black/30 border border-white/10 rounded px-3 py-2"
                value={songTitle}
                onChange={(e) => setSongTitle(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wide text-white/60 mb-1">Artist</label>
              <input
                className="w-full bg-black/30 border border-white/10 rounded px-3 py-2"
                value={songArtist}
                onChange={(e) => setSongArtist(e.target.value)}
              />
            </div>
          </div>
          <p className="text-xs text-white/50">
            You'll upload the actual audio file in the next step. For now, note the title and artist
            so credits and QA copyright checks have the metadata they need.
          </p>
        </section>
      )}

      {step === 3 && (
        <section className="space-y-4">
          <h2 className="text-lg font-serif">Fandoms</h2>
          <textarea
            className="w-full bg-black/30 border border-white/10 rounded px-3 py-2 min-h-40"
            value={fandomsText}
            onChange={(e) => setFandomsText(e.target.value)}
          />
          <p className="text-xs text-white/50">One fandom per line. You can tune per-act shares later.</p>
        </section>
      )}

      {step === 4 && (
        <section className="space-y-4">
          <h2 className="text-lg font-serif">Vibe and length</h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs uppercase tracking-wide text-white/60 mb-1">Vibe</label>
              <select
                className="w-full bg-black/30 border border-white/10 rounded px-3 py-2"
                value={vibe}
                onChange={(e) => setVibe(e.target.value as Vibe)}
              >
                {[
                  "action",
                  "emotional",
                  "hype",
                  "sad",
                  "funny",
                  "mixed",
                  "cinematic",
                  "horror",
                  "romantic",
                  "triumphant",
                ].map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wide text-white/60 mb-1">Length (seconds)</label>
              <input
                type="number"
                min={15}
                max={900}
                className="w-full bg-black/30 border border-white/10 rounded px-3 py-2"
                value={length}
                onChange={(e) => setLength(parseInt(e.target.value, 10) || 0)}
              />
            </div>
          </div>
        </section>
      )}

      {step === 5 && (
        <section className="space-y-4">
          <h2 className="text-lg font-serif">Platform</h2>
          <div className="grid grid-cols-3 gap-2">
            {(Object.keys(PLATFORM_PRESETS) as Platform[]).map((p) => (
              <button
                type="button"
                key={p}
                onClick={() => setPlatform(p)}
                className={`rounded border px-3 py-3 text-left text-sm ${
                  platform === p
                    ? "border-[var(--color-accent,#ff5a1f)] bg-white/5"
                    : "border-white/10 hover:bg-white/5"
                }`}
              >
                <div className="font-semibold">{p}</div>
                <div className="text-xs text-white/60">
                  {PLATFORM_PRESETS[p].width}x{PLATFORM_PRESETS[p].height} @ {PLATFORM_PRESETS[p].fps} /
                  {" "}
                  {PLATFORM_PRESETS[p].lufs} LUFS
                </div>
              </button>
            ))}
          </div>
          <p className="text-xs text-white/50">
            Pick a primary platform. You can generate exports for any of the others later with
            `ff export project all`.
          </p>
        </section>
      )}

      <div className="flex items-center gap-3 pt-4">
        {step > 1 && (
          <button className="px-4 py-2 rounded border border-white/10 hover:bg-white/5" onClick={prevStep}>
            Back
          </button>
        )}
        {step < 5 ? (
          <button
            className="px-4 py-2 rounded bg-[var(--color-accent,#ff5a1f)] text-black"
            onClick={nextStep}
            disabled={step === 1 && (!slug || !theme)}
          >
            Next
          </button>
        ) : (
          <button
            className="px-4 py-2 rounded bg-[var(--color-accent,#ff5a1f)] text-black disabled:opacity-50"
            onClick={submit}
            disabled={submitting || !slug || !theme}
          >
            Create project
          </button>
        )}
        {status && <span className="text-sm text-white/60 ml-2">{status}</span>}
      </div>
    </div>
  );
}
