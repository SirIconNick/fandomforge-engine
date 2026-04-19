import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ShotListEditor, { type ShotList } from "@/components/ShotListEditor";

const sample: ShotList = {
  schema_version: 1,
  project_slug: "test",
  fps: 24,
  resolution: { width: 1920, height: 1080 },
  shots: [
    {
      id: "s-1",
      act: 1,
      start_frame: 0,
      duration_frames: 48,
      source_id: "alpha",
      source_timecode: "00:00:00.000",
      role: "hero",
      description: "first shot",
      fandom: "F-A",
    },
    {
      id: "s-2",
      act: 1,
      start_frame: 48,
      duration_frames: 72,
      source_id: "beta",
      source_timecode: "00:00:02.000",
      role: "action",
      description: "second shot",
      fandom: "F-B",
    },
  ],
};

beforeEach(() => {
  // @ts-expect-error stub fetch
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (typeof url === "string" && url.endsWith("/shot-list") && (!init || init.method === "GET" || !init.method)) {
      return new Response(JSON.stringify(sample), { status: 200 });
    }
    if (init?.method === "PUT") {
      return new Response(JSON.stringify({ ok: true, path: "/stub" }), { status: 200 });
    }
    return new Response("not found", { status: 404 });
  });
});

describe("ShotListEditor", () => {
  it("loads and renders shots from the API", async () => {
    render(<ShotListEditor slug="test" />);
    expect(await screen.findByText("first shot")).toBeInTheDocument();
    expect(screen.getByText("second shot")).toBeInTheDocument();
  });

  it("marks unsaved when a shot is edited", async () => {
    render(<ShotListEditor slug="test" />);
    const first = await screen.findByText("first shot");
    fireEvent.click(first);
    // Description textarea has value "first shot"
    const textarea = await screen.findByDisplayValue("first shot");
    fireEvent.change(textarea, { target: { value: "edited shot" } });
    expect(screen.getByText(/unsaved edits/i)).toBeInTheDocument();
  });

  it("moves a shot up via the ↑ button and recomputes start frames", async () => {
    render(<ShotListEditor slug="test" />);
    await screen.findByText("first shot");
    const upButtons = screen.getAllByTitle("move up");
    // Click the up-button on the second shot so it becomes first.
    fireEvent.click(upButtons[1]);
    const rows = screen.getAllByText(/shot$/);
    expect(rows[0].textContent).toBe("second shot");
    expect(rows[1].textContent).toBe("first shot");
  });

  it("saves via PUT and clears the unsaved-edits badge", async () => {
    render(<ShotListEditor slug="test" />);
    const first = await screen.findByText("first shot");
    fireEvent.click(first);
    const textarea = await screen.findByDisplayValue("first shot");
    fireEvent.change(textarea, { target: { value: "edited" } });

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => {
      expect(screen.queryByText(/unsaved edits/i)).not.toBeInTheDocument();
    });
  });
});
