import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { ReviewReport } from "@/components/ReviewReport";
import type { PostRenderReview } from "@/lib/types/generated";

const sampleReport: PostRenderReview = {
  schema_version: 1,
  project_slug: "demo",
  video_path: "projects/demo/exports/graded.mp4",
  generated_at: "2026-04-19T14:00:00+00:00",
  overall: "yellow",
  overall_verdict: "warn",
  score: 82.1,
  grade: "B",
  ship_recommendation: "Reviewable with caveats — visual flagged.",
  dimensions: [
    {
      name: "technical",
      verdict: "pass",
      score: 100,
      findings: [],
      measurements: { width: 1920, height: 1080, fps: 24 },
    },
    {
      name: "visual",
      verdict: "warn",
      score: 69,
      findings: ["dark segment @ 45.2s → 45.6s (0.40s)"],
      measurements: { total_black_sec: 0.4 },
    },
    {
      name: "audio",
      verdict: "pass",
      score: 100,
      findings: [],
      measurements: {},
    },
    {
      name: "structural",
      verdict: "pass",
      score: 100,
      findings: [],
      measurements: {},
    },
    {
      name: "shot_list",
      verdict: "pass",
      score: 100,
      findings: [],
      measurements: {},
    },
  ],
};

describe("<ReviewReport />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the grade, score, and ship recommendation", () => {
    render(<ReviewReport slug="demo" initialReport={sampleReport} />);
    expect(screen.getByText("B")).toBeInTheDocument();
    expect(screen.getByText("82.1/100")).toBeInTheDocument();
    expect(
      screen.getByText(/Reviewable with caveats/)
    ).toBeInTheDocument();
  });

  it("renders each dimension card with findings", () => {
    render(<ReviewReport slug="demo" initialReport={sampleReport} />);
    const headings = screen
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(headings).toEqual(
      expect.arrayContaining(["technical", "visual", "audio", "structural", "shot list"])
    );
    expect(
      screen.getByText(/dark segment @ 45.2s/)
    ).toBeInTheDocument();
  });

  it("shows empty state and rerun button when no report exists", () => {
    render(<ReviewReport slug="demo" initialReport={null} />);
    expect(screen.getByText(/No review has run/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run review/i })).toBeInTheDocument();
  });

  it("fetches and replaces report on rerun click", async () => {
    const updated: PostRenderReview = {
      ...sampleReport,
      grade: "A",
      score: 95,
      overall: "green",
      overall_verdict: "pass",
      ship_recommendation: "Green across the board.",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ ok: true, report: updated }),
      })
    );
    render(<ReviewReport slug="demo" initialReport={sampleReport} />);
    fireEvent.click(screen.getByRole("button", { name: /Re-run/i }));
    await waitFor(() => expect(screen.getByText("A")).toBeInTheDocument());
    expect(screen.getByText("95.0/100")).toBeInTheDocument();
  });
});
