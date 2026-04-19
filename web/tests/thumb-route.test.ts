import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import { GET } from "@/app/api/project/[slug]/thumb/route";
import { PROJECT_ROOT } from "@/lib/fs";

const TEST_SLUG = "__test_thumb_route__";
const projDir = path.join(PROJECT_ROOT, "projects", TEST_SLUG);

function thumbReq(query: string): Request {
  return new Request(`http://localhost/api/project/${TEST_SLUG}/thumb${query}`);
}

async function cleanup() {
  try {
    await fs.rm(projDir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
}

describe("/api/project/[slug]/thumb", () => {
  beforeAll(async () => {
    await cleanup();
  });
  afterAll(async () => {
    await cleanup();
  });

  it("returns 400 when source is missing", async () => {
    const res = await GET(thumbReq("?time=10"), {
      params: Promise.resolve({ slug: TEST_SLUG }),
    });
    expect(res.status).toBe(400);
  });

  it("returns 404 when clip-cache directory does not exist", async () => {
    await fs.mkdir(projDir, { recursive: true });
    const res = await GET(thumbReq("?source=S01&time=5"), {
      params: Promise.resolve({ slug: TEST_SLUG }),
    });
    expect(res.status).toBe(404);
  });

  it("serves a cached frame when one is present at the mapped index", async () => {
    const cache = path.join(projDir, ".clip-cache", "S01_frames");
    await fs.mkdir(cache, { recursive: true });
    // 5s interval, time=5 -> index 1 -> frame_000002.jpg
    const jpgMagic = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]);
    await fs.writeFile(path.join(cache, "frame_000002.jpg"), jpgMagic);

    const res = await GET(thumbReq("?source=S01&time=5"), {
      params: Promise.resolve({ slug: TEST_SLUG }),
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("image/jpeg");
    const buf = await res.arrayBuffer();
    expect(buf.byteLength).toBe(jpgMagic.length);
  });

  it("falls back to the middle frame when the exact index is not cached", async () => {
    const cache = path.join(projDir, ".clip-cache", "S02_frames");
    await fs.mkdir(cache, { recursive: true });
    for (const name of ["frame_000010.jpg", "frame_000020.jpg", "frame_000030.jpg"]) {
      await fs.writeFile(path.join(cache, name), Buffer.from([0xff, 0xd8, 0xff]));
    }
    const res = await GET(thumbReq("?source=S02&time=9999"), {
      params: Promise.resolve({ slug: TEST_SLUG }),
    });
    expect(res.status).toBe(200);
  });
});
