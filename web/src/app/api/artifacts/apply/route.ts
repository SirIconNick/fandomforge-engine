import { NextRequest, NextResponse } from "next/server";
import { applyPatch, type Operation } from "fast-json-patch";
import {
  artifactPath,
  ensureProjectDirs,
  historyPath,
  projectExists,
  readArtifact,
  writeArtifactAtomic,
} from "@/lib/project-context";
import { appendJsonLine, sha256 } from "@/lib/atomic-write";
import { validateArtifact, type ArtifactType } from "@/lib/schemas";

interface ApplyRequest {
  project_slug: string;
  artifact: ArtifactType;
  patch: Operation[];
  expected_sha256?: string | null;
  rationale?: string;
  expert_slug?: string;
  accepted_op_indices?: number[];
}

const mutexes = new Map<string, Promise<unknown>>();

async function withArtifactLock<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const prev = mutexes.get(key) ?? Promise.resolve();
  let release!: () => void;
  const next = new Promise<void>((resolve) => {
    release = resolve;
  });
  mutexes.set(
    key,
    prev.then(() => next)
  );
  try {
    await prev;
    const result = await fn();
    return result;
  } finally {
    release();
    if (mutexes.get(key) === next) mutexes.delete(key);
  }
}

export async function POST(req: NextRequest) {
  let body: ApplyRequest;
  try {
    body = (await req.json()) as ApplyRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { project_slug, artifact, patch } = body;
  if (!project_slug || !artifact || !Array.isArray(patch)) {
    return NextResponse.json(
      { error: "Missing required fields: project_slug, artifact, patch" },
      { status: 400 }
    );
  }

  if (!(await projectExists(project_slug))) {
    return NextResponse.json(
      { error: `Project not found: ${project_slug}` },
      { status: 404 }
    );
  }

  const lockKey = artifactPath(project_slug, artifact);

  return withArtifactLock(lockKey, async () => {
    await ensureProjectDirs(project_slug);
    const current = await readArtifact(project_slug, artifact);

    if (
      current.exists &&
      body.expected_sha256 &&
      current.sha256 !== body.expected_sha256
    ) {
      return NextResponse.json(
        {
          error: "Artifact was modified since it was read. Reload and try again.",
          code: "sha_mismatch",
          current_sha256: current.sha256,
        },
        { status: 409 }
      );
    }

    const filteredPatch =
      Array.isArray(body.accepted_op_indices) && body.accepted_op_indices.length > 0
        ? body.accepted_op_indices
            .map((i) => patch[i])
            .filter((op): op is Operation => Boolean(op))
        : patch;

    const starting = current.data ?? {};
    const preSnapshot = JSON.parse(JSON.stringify(starting));
    let nextDoc: unknown;
    try {
      const result = applyPatch(
        JSON.parse(JSON.stringify(starting)),
        filteredPatch,
        true,
        false
      );
      nextDoc = result.newDocument;
    } catch (err) {
      return NextResponse.json(
        {
          error: `Patch failed to apply: ${(err as Error).message}`,
          code: "patch_failed",
        },
        { status: 422 }
      );
    }

    function resolveJsonPointer(doc: unknown, pointer: string): unknown {
      if (!pointer || pointer === "/") return doc;
      const parts = pointer
        .split("/")
        .slice(1)
        .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
      let cursor: unknown = doc;
      for (const part of parts) {
        if (cursor == null) return undefined;
        if (Array.isArray(cursor)) cursor = cursor[Number(part)];
        else if (typeof cursor === "object")
          cursor = (cursor as Record<string, unknown>)[part];
        else return undefined;
      }
      return cursor;
    }

    const opsWithBefore = filteredPatch.map((op) => {
      const before_value = resolveJsonPointer(preSnapshot, op.path);
      return {
        ...op,
        before_value: before_value === undefined ? null : before_value,
      };
    });

    const validation = await validateArtifact(artifact, nextDoc);
    if (!validation.ok) {
      return NextResponse.json(
        {
          error: "Patched document failed schema validation.",
          code: "schema_failed",
          schema_errors: validation.errors,
        },
        { status: 422 }
      );
    }

    const written = await writeArtifactAtomic(project_slug, artifact, nextDoc);

    await appendJsonLine(historyPath(project_slug, artifact), {
      ts: new Date().toISOString(),
      expert_slug: body.expert_slug ?? null,
      rationale: body.rationale ?? null,
      before_sha256: current.sha256,
      after_sha256: written.sha256,
      applied_ops: opsWithBefore,
      accepted_op_indices: body.accepted_op_indices ?? null,
    });

    return NextResponse.json({
      ok: true,
      artifact,
      project_slug,
      before_sha256: current.sha256,
      after_sha256: written.sha256,
      bytes: written.raw?.length ?? 0,
    });
  });
}

export async function GET() {
  return NextResponse.json(
    { error: "Use POST to apply artifact patches." },
    { status: 405 }
  );
}

export function computeSha(content: string) {
  return sha256(content);
}
