import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  atomicWriteJson,
  atomicWriteString,
  sha256,
  appendJsonLine,
  readJsonLines,
} from "@/lib/atomic-write";

describe("atomic-write", () => {
  let dir: string;

  beforeEach(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), "ff-atomic-"));
  });

  afterEach(async () => {
    await fs.rm(dir, { recursive: true, force: true });
  });

  it("atomicWriteJson writes a valid JSON file and survives rename", async () => {
    const target = path.join(dir, "nested", "foo.json");
    await atomicWriteJson(target, { hello: "world", n: 42 });
    const raw = await fs.readFile(target, "utf8");
    expect(JSON.parse(raw)).toEqual({ hello: "world", n: 42 });
    expect(raw.endsWith("\n")).toBe(true);
  });

  it("atomicWriteString does not leave .tmp files behind on success", async () => {
    const target = path.join(dir, "hello.txt");
    await atomicWriteString(target, "hi");
    const entries = await fs.readdir(dir);
    expect(entries).toEqual(["hello.txt"]);
  });

  it("atomicWriteJson overwrites existing files cleanly", async () => {
    const target = path.join(dir, "repeat.json");
    await atomicWriteJson(target, { a: 1 });
    await atomicWriteJson(target, { b: 2 });
    const raw = await fs.readFile(target, "utf8");
    expect(JSON.parse(raw)).toEqual({ b: 2 });
  });

  it("sha256 is deterministic and encoding-safe", () => {
    expect(sha256("hello")).toBe(sha256("hello"));
    expect(sha256("hello")).not.toBe(sha256("hello\n"));
    expect(sha256("")).toHaveLength(64);
  });

  it("appendJsonLine and readJsonLines round-trip", async () => {
    const jl = path.join(dir, "journal.jsonl");
    await appendJsonLine(jl, { step: 1 });
    await appendJsonLine(jl, { step: 2, note: "after" });
    const lines = await readJsonLines(jl);
    expect(lines).toEqual([{ step: 1 }, { step: 2, note: "after" }]);
  });

  it("readJsonLines returns empty array for missing file", async () => {
    const lines = await readJsonLines(path.join(dir, "never.jsonl"));
    expect(lines).toEqual([]);
  });
});
