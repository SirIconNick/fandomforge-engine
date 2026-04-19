import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UploadDropzone } from "@/components/UploadDropzone";

describe("UploadDropzone", () => {
  beforeEach(() => {
    // @ts-expect-error – stub XHR
    globalThis.XMLHttpRequest = class MockXHR {
      public status = 200;
      public responseText = JSON.stringify({
        saved: [{ name: "clip.mp4", bytes: 1234, path: "/raw/clip.mp4" }],
        rejected: [],
        target_dir: "raw",
      });
      public upload = { onprogress: null as ((ev: ProgressEvent) => void) | null };
      public onload: (() => void) | null = null;
      public onerror: (() => void) | null = null;
      open() {}
      send() {
        setTimeout(() => {
          this.upload.onprogress?.({ lengthComputable: true, loaded: 1234, total: 1234 } as ProgressEvent);
          this.onload?.();
        }, 0);
      }
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the target dir selector and file input", () => {
    render(<UploadDropzone slug="test-slug" />);
    expect(screen.getByText(/Drop files here/i)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    // Native file input has type=file; search by display value or role.
    const fileInput = document.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
  });

  it("shows saved files after successful upload", async () => {
    render(<UploadDropzone slug="test-slug" />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "clip.mp4", { type: "video/mp4" });
    Object.defineProperty(fileInput, "files", { value: [file], writable: false });
    fireEvent.change(fileInput);

    await new Promise((r) => setTimeout(r, 10));
    expect(await screen.findByText(/Saved/i)).toBeInTheDocument();
    expect(await screen.findByText(/clip\.mp4/)).toBeInTheDocument();
  });

  it("changes target_dir when select is changed", () => {
    render(<UploadDropzone slug="test-slug" />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "dialogue" } });
    expect(select.value).toBe("dialogue");
  });
});
