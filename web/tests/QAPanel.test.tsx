import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import QAPanel from "@/app/projects/[slug]/qa/QAPanel";

const passingReport = {
  schema_version: 1,
  project_slug: "demo",
  stage: "pre-export",
  status: "pass",
  rules: [
    { id: "qa.refs", name: "Unresolved references", level: "block", status: "pass",
      message: "all shots resolve" },
    { id: "qa.cliche", name: "Cliche shots", level: "block", status: "pass",
      message: "no cliche shots" },
  ],
  summary: { total: 2, passed: 2, warned: 0, failed: 0, overridden: 0 },
};

const failingReport = {
  ...passingReport,
  status: "fail",
  rules: [
    { id: "qa.refs", name: "Unresolved references", level: "block", status: "fail",
      message: "2 shots reference sources not in the catalog",
      evidence: { count: 2 } },
  ],
  summary: { total: 1, passed: 0, warned: 0, failed: 1, overridden: 0 },
};

describe("QAPanel", () => {
  beforeEach(() => {
    // @ts-expect-error stub fetch
    globalThis.fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Response(
          JSON.stringify({ ok: true, report: passingReport, stdout: "", stderr: "" }),
          { status: 200 }
        );
      }
      return new Response("not found", { status: 404 });
    });
  });

  it("renders passing status from initialReport", () => {
    render(<QAPanel slug="demo" initialReport={passingReport} />);
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("Unresolved references")).toBeInTheDocument();
  });

  it("shows failing status and evidence from initialReport", () => {
    render(<QAPanel slug="demo" initialReport={failingReport} />);
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText(/2 shots reference sources/)).toBeInTheDocument();
    // The override input should be present for block-level failures.
    expect(screen.getByPlaceholderText(/reason for override/i)).toBeInTheDocument();
  });

  it("runs the gate via POST when Run gate is clicked", async () => {
    render(<QAPanel slug="demo" initialReport={null} />);
    fireEvent.click(screen.getByText("Run gate"));
    await waitFor(() => {
      expect(screen.getByText("PASS")).toBeInTheDocument();
    });
  });
});
