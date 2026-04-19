import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ArtifactEditor from "@/components/ArtifactEditor";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

describe("<ArtifactEditor />", () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });

  it("loads existing artifact data into the textarea", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              ok: true,
              exists: true,
              data: { schema_version: 1, project_slug: "demo", target_color_space: "Rec.709", per_source: {} },
              sha256: "abc",
            }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, errors: null }) });
    });

    render(
      <ArtifactEditor
        projectSlug="demo"
        artifactType="color-plan"
        title="color-plan.json"
      />
    );

    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toContain("Rec.709");
    });
    expect(screen.getByText(/on disk/i)).toBeInTheDocument();
  });

  it("shows 'new' badge when the artifact does not yet exist", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              ok: true,
              exists: false,
              data: null,
              sha256: null,
            }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, errors: null }) });
    });

    render(
      <ArtifactEditor
        projectSlug="demo"
        artifactType="color-plan"
        seed={{ schema_version: 1, project_slug: "demo", target_color_space: "Rec.709", per_source: {} }}
      />
    );

    await waitFor(() => expect(screen.getByText(/new/i)).toBeInTheDocument());
  });

  it("renders a parse-error banner when the JSON is malformed", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ ok: true, exists: true, data: { a: 1 }, sha256: "abc" }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, errors: null }) });
    });

    render(<ArtifactEditor projectSlug="demo" artifactType="color-plan" />);

    const textarea = await screen.findByRole("textbox");
    fireEvent.change(textarea, { target: { value: "{ not json" } });

    await waitFor(() => expect(screen.getByText(/JSON invalid/i)).toBeInTheDocument());
  });

  it("disables save when the document is invalid per the schema", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/api/artifacts/read")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, exists: true, data: {}, sha256: "abc" }),
        });
      }
      if (url.includes("/api/artifacts/validate")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              ok: false,
              errors: [{ instancePath: "/target_color_space", message: "wrong" }],
            }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });

    render(<ArtifactEditor projectSlug="demo" artifactType="color-plan" />);
    const textarea = await screen.findByRole("textbox");
    fireEvent.change(textarea, { target: { value: '{"schema_version":1}' } });

    await waitFor(() => expect(screen.getByText(/Schema errors/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Save/i })).toBeDisabled();
  });
});
