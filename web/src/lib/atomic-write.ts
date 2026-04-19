import { promises as fs } from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export async function atomicWriteJson(
  filePath: string,
  data: unknown,
  indent = 2
): Promise<void> {
  await atomicWriteString(filePath, JSON.stringify(data, null, indent) + "\n");
}

export async function atomicWriteString(
  filePath: string,
  content: string
): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp.${process.pid}.${crypto.randomBytes(6).toString("hex")}`;
  const fh = await fs.open(tmp, "w");
  try {
    await fh.writeFile(content, "utf8");
    await fh.sync();
  } finally {
    await fh.close();
  }
  await fs.rename(tmp, filePath);
}

export function sha256(content: string): string {
  return crypto.createHash("sha256").update(content, "utf8").digest("hex");
}

export async function appendJsonLine(
  filePath: string,
  entry: unknown
): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const line = JSON.stringify(entry) + "\n";
  await fs.appendFile(filePath, line, "utf8");
}

export async function readJsonLines(filePath: string): Promise<unknown[]> {
  try {
    const raw = await fs.readFile(filePath, "utf8");
    const lines = raw.split("\n").filter(Boolean);
    return lines.map((l) => JSON.parse(l));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}
