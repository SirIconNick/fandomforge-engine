"use client";

import { useEffect, useState } from "react";

interface Step {
  id: string;
  title: string;
  body: string;
  cta?: { label: string; href: string };
  highlight_selector?: string;
  recommended?: boolean;
}

const STORAGE_KEY = "ff.onboarding.completed";

function stepsFor(projectSlug: string): Step[] {
  return [
    {
      id: "welcome",
      title: "Welcome to FandomForge",
      body:
        "This is a fresh project. The fastest path to a finished draft is the auto-pilot — it runs the whole pipeline for you. You can also do each step by hand if you want finer control.",
    },
    {
      id: "autopilot",
      title: "Auto-pilot (recommended)",
      body:
        "One click. Beat analysis, edit-plan, shot-list, emotion arc, QA gate. Idempotent, so you can stop and resume. Most users should start here.",
      cta: { label: "Try Auto-pilot →", href: `/projects/${projectSlug}/autopilot` },
      recommended: true,
    },
    {
      id: "upload",
      title: "Or upload media first",
      body:
        "If you prefer to ingest sources manually, drop your song into assets/ and source clips into raw/. The rest of the pipeline reads from there.",
      cta: { label: "Upload zone", href: `/projects/${projectSlug}#upload` },
    },
    {
      id: "experts",
      title: "Ask an expert",
      body:
        "12 specialist agents cover every dimension of an edit (beats, color, shots, transitions, etc). Chat with any of them — they can propose structured edits to your artifacts with per-op review.",
      cta: { label: "Expert chat", href: `/experts/chat/edit-strategist?project=${projectSlug}` },
    },
    {
      id: "draft-shots",
      title: "Draft a shot list",
      body:
        "If you have an edit-plan and beat-map, the shot-proposer will sketch a first-pass shot list — hero shots on drops, cuts on downbeats, placeholders for missing sources.",
      cta: { label: "Draft shot list", href: `/projects/${projectSlug}/propose-shots` },
    },
    {
      id: "preview",
      title: "Rough preview (no render)",
      body:
        "Before running the render pipeline, the rough preview plays your shot-list against the song — you see the rhythm working in seconds instead of minutes.",
      cta: { label: "Rough preview", href: `/projects/${projectSlug}/preview` },
    },
    {
      id: "pipeline",
      title: "Run pipeline / export",
      body:
        "When you're happy with the plan, run the full pipeline to produce a graded MP4 and NLE-importable XML. Or fine-tune in DaVinci Resolve / Premiere / CapCut / Vegas.",
      cta: { label: "Pipeline runner", href: `/pipeline/${projectSlug}` },
    },
  ];
}

export interface OnboardingTourProps {
  projectSlug: string;
  forceOpen?: boolean;
  onClose?: () => void;
}

export default function OnboardingTour({
  projectSlug,
  forceOpen,
  onClose,
}: OnboardingTourProps) {
  const steps = stepsFor(projectSlug);
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (forceOpen) {
      setOpen(true);
      setIndex(0);
      return;
    }
    try {
      const done = window.localStorage.getItem(STORAGE_KEY);
      if (!done) setOpen(true);
    } catch {
      setOpen(true);
    }
  }, [forceOpen]);

  function close() {
    try {
      window.localStorage.setItem(STORAGE_KEY, new Date().toISOString());
    } catch { /* best-effort */ }
    setOpen(false);
    onClose?.();
  }

  function next() {
    if (index >= steps.length - 1) {
      close();
      return;
    }
    setIndex(index + 1);
  }

  function prev() {
    if (index > 0) setIndex(index - 1);
  }

  if (!open) return null;
  const step = steps[index];
  if (!step) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="tour-title"
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
    >
      <div className="w-full max-w-md bg-[var(--color-ink,#0b0b0f)] border border-[var(--color-forge,#ff5a1f)]/40 rounded-lg p-5 shadow-2xl">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide bg-[var(--color-forge,#ff5a1f)]/20 text-[var(--color-forge,#ff5a1f)] px-2 py-0.5 rounded">
              {index + 1} / {steps.length}
            </span>
            {step.recommended && (
              <span className="text-[10px] uppercase tracking-wide bg-green-500/20 text-green-300 px-2 py-0.5 rounded">
                recommended
              </span>
            )}
          </div>
          <button
            onClick={close}
            aria-label="Close tour"
            className="text-white/40 hover:text-white text-sm"
          >
            skip
          </button>
        </div>

        <h2 id="tour-title" className="text-xl font-display mb-2">
          {step.title}
        </h2>
        <p className="text-sm text-white/80 mb-4">{step.body}</p>

        {step.cta && (
          <a
            href={step.cta.href}
            className="inline-block mb-3 px-3 py-1.5 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-xs"
          >
            {step.cta.label}
          </a>
        )}

        <div className="flex items-center justify-between pt-3 border-t border-white/10">
          <button
            onClick={prev}
            disabled={index === 0}
            className="text-xs text-white/60 hover:text-white disabled:opacity-30"
          >
            ← back
          </button>
          <div className="flex gap-1" aria-hidden="true">
            {steps.map((_, i) => (
              <span
                key={i}
                className={`w-1.5 h-1.5 rounded-full ${
                  i === index ? "bg-[var(--color-forge,#ff5a1f)]" : "bg-white/20"
                }`}
              />
            ))}
          </div>
          <button
            onClick={next}
            className="text-xs font-semibold text-[var(--color-forge,#ff5a1f)]"
          >
            {index === steps.length - 1 ? "done" : "next →"}
          </button>
        </div>
      </div>
    </div>
  );
}
