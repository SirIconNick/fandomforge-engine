import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ArtifactDiffReview from "@/components/ArtifactDiffReview";
import type { Operation } from "fast-json-patch";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

function buildPatch() {
  return {
    tool_use_id: "toolu_x",
    expert_slug: "color-grader",
    artifact: "color-plan",
    rationale: "warmer grade across the emotional act",
    patch: [
      { op: "replace", path: "/target_color_space", value: "sRGB" },
      { op: "add", path: "/global_lut_intensity", value: 0.8 },
    ] as Operation[],
  };
}

describe("<ArtifactDiffReview />", () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });

  it("renders rationale, ops, and a default apply button", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              ok: true,
              exists: true,
              data: { schema_version: 1, target_color_space: "Rec.709", per_source: {} },
              sha256: "abc123",
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ok: true, errors: null }),
      });
    });

    render(<ArtifactDiffReview projectSlug="my-edit" patch={buildPatch()} />);

    expect(screen.getByText(/proposed patch/i)).toBeInTheDocument();
    expect(screen.getByText(/color-plan/)).toBeInTheDocument();
    expect(screen.getByText(/warmer grade across the emotional act/)).toBeInTheDocument();
    expect(screen.getByText("replace")).toBeInTheDocument();
    expect(screen.getByText("add")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Apply 2\/2 ops/i })).toBeInTheDocument()
    );
  });

  it("posts apply with only accepted ops when one is unchecked", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === "string" && url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ ok: true, exists: true, data: {}, sha256: "abc" }),
        });
      }
      if (typeof url === "string" && url.includes("/api/artifacts/validate")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, errors: null }) });
      }
      if (typeof url === "string" && url.includes("/api/artifacts/apply")) {
        const body = init?.body ? JSON.parse(init.body as string) : null;
        expect(body.accepted_op_indices).toEqual([0]);
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, after_sha256: "xyz", bytes: 42 }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });

    render(<ArtifactDiffReview projectSlug="my-edit" patch={buildPatch()} />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Apply 2\/2 ops/i })).toBeInTheDocument()
    );

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[1]!);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Apply 1\/2 ops/i })).toBeInTheDocument()
    );

    fireEvent.click(screen.getByRole("button", { name: /Apply 1\/2 ops/i }));

    await waitFor(() => expect(screen.getByText(/Applied/)).toBeInTheDocument());
  });

  it("disables apply when the schema validation fails", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ ok: true, exists: true, data: {}, sha256: "abc" }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            ok: false,
            errors: [{ instancePath: "/target_color_space", message: "bad value" }],
          }),
      });
    });

    render(<ArtifactDiffReview projectSlug="my-edit" patch={buildPatch()} />);

    await waitFor(() =>
      expect(screen.getByText(/Schema validation failed/)).toBeInTheDocument()
    );
    const applyButton = screen.getByRole("button", { name: /Apply 2\/2 ops/i });
    expect(applyButton).toBeDisabled();
  });

  it("reject button disables apply and shows rejected state", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({ ok: true, exists: true, data: {}, sha256: "abc", errors: null }),
      })
    );
    render(<ArtifactDiffReview projectSlug="my-edit" patch={buildPatch()} />);
    fireEvent.click(screen.getByRole("button", { name: /Reject all/i }));
    expect(screen.getByText(/Rejected\./)).toBeInTheDocument();
  });
});
