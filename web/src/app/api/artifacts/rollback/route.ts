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
import { appendJsonLine, readJsonLines, sha256 } from "@/lib/atomic-write";
import { validateArtifact, type ArtifactType } from "@/lib/schemas";

interface RollbackRequest {
  project_slug: string;
  artifact: ArtifactType;
  steps?: number;
}

type JournalOp = Operation & { before_value?: unknown };

interface JournalEntry {
  ts: string;
  expert_slug: string | null;
  rationale: string | null;
  before_sha256: string | null;
  after_sha256: string | null;
  applied_ops: JournalOp[];
  accepted_op_indices: number[] | null;
}

function invertOp(op: JournalOp): Operation[] {
  // Uses the before_value captured at apply time for correct reversal.
  switch (op.op) {
    case "add":
      return [{ op: "remove", path: op.path }];
    case "remove":
      return [
        { op: "add", path: op.path, value: (op.before_value as unknown) ?? null },
      ];
    case "replace": {
      const prev = op.before_value;
      if (prev === undefined) {
        return [{ op: "remove", path: op.path }];
      }
      return [{ op: "replace", path: op.path, value: prev as unknown }];
    }
    case "copy":
      return [{ op: "remove", path: op.path }];
    case "move":
      return [{ op: "move", from: op.path, path: op.from ?? "/" }];
    default:
      return [];
  }
}

export async function POST(req: NextRequest) {
  let body: RollbackRequest;
  try {
    body = (await req.json()) as RollbackRequest;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { project_slug, artifact } = body;
  const steps = Math.max(1, Math.floor(body.steps ?? 1));
  if (!project_slug || !artifact) {
    return NextResponse.json(
      { error: "Missing required fields: project_slug, artifact" },
      { status: 400 }
    );
  }

  if (!(await projectExists(project_slug))) {
    return NextResponse.json(
      { error: `Project not found: ${project_slug}` },
      { status: 404 }
    );
  }

  await ensureProjectDirs(project_slug);

  const journal = (await readJsonLines(
    historyPath(project_slug, artifact)
  )) as JournalEntry[];

  if (journal.length < steps) {
    return NextResponse.json(
      { error: `Only ${journal.length} journal entries; cannot roll back ${steps}.` },
      { status: 409 }
    );
  }

  const current = await readArtifact(project_slug, artifact);
  let doc: unknown = current.data ?? {};

  const rolled: JournalEntry[] = [];
  for (let i = 0; i < steps; i++) {
    const entry = journal[journal.length - 1 - i];
    if (!entry) break;
    const inverse: Operation[] = [];
    for (let j = entry.applied_ops.length - 1; j >= 0; j--) {
      const op = entry.applied_ops[j];
      if (!op) continue;
      inverse.push(...invertOp(op));
    }
    if (inverse.length === 0) {
      return NextResponse.json(
        {
          error: `Journal entry at index ${journal.length - 1 - i} could not be inverted (unknown op types).`,
        },
        { status: 422 }
      );
    }
    try {
      const result = applyPatch(
        JSON.parse(JSON.stringify(doc)),
        inverse,
        false,
        false
      );
      doc = result.newDocument;
    } catch (err) {
      return NextResponse.json(
        {
          error: `Inverse patch failed: ${(err as Error).message}`,
        },
        { status: 422 }
      );
    }
    rolled.push(entry);
  }

  const validation = await validateArtifact(artifact, doc);
  if (!validation.ok) {
    return NextResponse.json(
      {
        error: "Rolled-back document failed schema validation.",
        schema_errors: validation.errors,
      },
      { status: 422 }
    );
  }

  const written = await writeArtifactAtomic(project_slug, artifact, doc);

  await appendJsonLine(historyPath(project_slug, artifact), {
    ts: new Date().toISOString(),
    expert_slug: null,
    rationale: `rollback of ${steps} entr${steps === 1 ? "y" : "ies"}`,
    before_sha256: current.sha256,
    after_sha256: written.sha256,
    applied_ops: [],
    accepted_op_indices: null,
    rolled_back_entries: rolled.length,
  });

  return NextResponse.json({
    ok: true,
    artifact,
    project_slug,
    steps_rolled_back: rolled.length,
    after_sha256: written.sha256,
  });
}

export { sha256 as _sha256ForTests };
