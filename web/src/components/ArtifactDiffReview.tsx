"use client";

import { useEffect, useMemo, useState } from "react";
import { applyPatch, type Operation } from "fast-json-patch";

interface ProposedPatchLike {
  tool_use_id: string;
  expert_slug: string;
  artifact: string;
  rationale: string;
  patch: Operation[];
}

export interface ArtifactDiffReviewProps {
  projectSlug: string;
  patch: ProposedPatchLike;
  onApplied?: (after: { sha256: string | null }) => void;
  onRejected?: () => void;
}

interface ArtifactSnapshot {
  ok: boolean;
  exists: boolean;
  data: unknown;
  sha256: string | null;
}

interface SchemaError {
  instancePath?: string;
  message?: string;
  keyword?: string;
}

type ValidationState =
  | { kind: "idle" }
  | { kind: "ok" }
  | { kind: "error"; errors: SchemaError[] }
  | { kind: "patch_failed"; message: string };

function shortValue(v: unknown): string {
  if (v === undefined) return "—";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 140 ? s.slice(0, 137) + "…" : s;
}

function locate(doc: unknown, jsonPointer: string): unknown {
  if (!jsonPointer || jsonPointer === "/") return doc;
  const parts = jsonPointer
    .split("/")
    .slice(1)
    .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
  let cursor: unknown = doc;
  for (const part of parts) {
    if (cursor == null) return undefined;
    if (Array.isArray(cursor)) {
      cursor = cursor[Number(part)];
    } else if (typeof cursor === "object") {
      cursor = (cursor as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return cursor;
}

export default function ArtifactDiffReview({
  projectSlug,
  patch,
  onApplied,
  onRejected,
}: ArtifactDiffReviewProps) {
  const [snapshot, setSnapshot] = useState<ArtifactSnapshot | null>(null);
  const [accepted, setAccepted] = useState<boolean[]>(
    () => patch.patch.map(() => true)
  );
  const [validation, setValidation] = useState<ValidationState>({ kind: "idle" });
  const [status, setStatus] = useState<"idle" | "applying" | "applied" | "rejected" | "error">(
    "idle"
  );
  const [errorText, setErrorText] = useState("");
  const [rawOpen, setRawOpen] = useState(false);

  const acceptedOps = useMemo(
    () => patch.patch.filter((_, i) => accepted[i]),
    [patch.patch, accepted]
  );

  useEffect(() => {
    const url = `/api/artifacts/read?project=${encodeURIComponent(projectSlug)}&artifact=${encodeURIComponent(patch.artifact)}`;
    fetch(url)
      .then((r) => r.json())
      .then((d: ArtifactSnapshot) => setSnapshot(d))
      .catch(() =>
        setSnapshot({ ok: false, exists: false, data: null, sha256: null })
      );
  }, [projectSlug, patch.artifact]);

  useEffect(() => {
    if (!snapshot) return;
    if (acceptedOps.length === 0) {
      setValidation({ kind: "idle" });
      return;
    }
    try {
      const starting = snapshot.data ?? {};
      const cloned = JSON.parse(JSON.stringify(starting));
      const next = applyPatch(cloned, acceptedOps, false, false).newDocument;
      fetch("/api/artifacts/validate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ artifact: patch.artifact, data: next }),
      })
        .then((r) => r.json())
        .then((d: { ok: boolean; errors: SchemaError[] | null }) => {
          if (d.ok) setValidation({ kind: "ok" });
          else setValidation({ kind: "error", errors: d.errors ?? [] });
        })
        .catch(() => setValidation({ kind: "idle" }));
    } catch (err) {
      setValidation({ kind: "patch_failed", message: (err as Error).message });
    }
  }, [snapshot, acceptedOps, patch.artifact]);

  async function apply() {
    if (status === "applying") return;
    setStatus("applying");
    setErrorText("");
    try {
      const res = await fetch("/api/artifacts/apply", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_slug: projectSlug,
          artifact: patch.artifact,
          patch: patch.patch,
          expected_sha256: snapshot?.sha256 ?? null,
          rationale: patch.rationale,
          expert_slug: patch.expert_slug,
          accepted_op_indices: accepted.map((a, i) => (a ? i : -1)).filter((i) => i >= 0),
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setStatus("error");
        setErrorText(
          body.error ??
            `apply failed (${res.status})` +
              (body.schema_errors ? ` — ${JSON.stringify(body.schema_errors)}` : "")
        );
        return;
      }
      setStatus("applied");
      onApplied?.({ sha256: body.after_sha256 });
    } catch (err) {
      setStatus("error");
      setErrorText((err as Error).message);
    }
  }

  function reject() {
    setStatus("rejected");
    onRejected?.();
  }

  const allRejected = accepted.every((a) => !a);
  const canApply =
    status === "idle" &&
    acceptedOps.length > 0 &&
    (validation.kind === "ok" || validation.kind === "idle");

  return (
    <div className="rounded border border-[var(--color-forge,#ff5a1f)]/40 bg-[var(--color-forge,#ff5a1f)]/5 p-3 my-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide bg-[var(--color-forge,#ff5a1f)]/20 text-[var(--color-forge,#ff5a1f)] px-2 py-0.5 rounded">
            proposed patch
          </span>
          <span className="text-sm font-semibold">{patch.artifact}</span>
          <span className="text-xs text-white/50">by {patch.expert_slug}</span>
        </div>
        <ValidationBadge state={validation} />
      </div>

      {patch.rationale && (
        <p className="text-xs text-white/70 mb-2 whitespace-pre-wrap">{patch.rationale}</p>
      )}

      <div className="space-y-1 mb-3">
        {patch.patch.map((op, i) => (
          <OpRow
            key={i}
            op={op}
            checked={accepted[i] ?? false}
            before={snapshot ? locate(snapshot.data, op.path) : undefined}
            disabled={status !== "idle"}
            onToggle={(next) =>
              setAccepted((prev) => prev.map((v, idx) => (idx === i ? next : v)))
            }
          />
        ))}
      </div>

      {validation.kind === "error" && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 mb-2">
          <div className="font-semibold mb-1">Schema validation failed:</div>
          <ul className="list-disc ml-4">
            {validation.errors.slice(0, 5).map((e, i) => (
              <li key={i}>
                <code className="text-red-200">{e.instancePath || "/"}</code>{" "}
                {e.message ?? e.keyword}
              </li>
            ))}
          </ul>
        </div>
      )}

      {validation.kind === "patch_failed" && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 mb-2">
          Patch failed locally: {validation.message}
        </div>
      )}

      {errorText && (
        <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 mb-2 whitespace-pre-wrap">
          {errorText}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={apply}
          disabled={!canApply || allRejected}
          className="px-3 py-1.5 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-xs disabled:opacity-40"
        >
          {status === "applying"
            ? "Applying…"
            : status === "applied"
              ? "Applied"
              : `Apply ${acceptedOps.length}/${patch.patch.length} op${patch.patch.length === 1 ? "" : "s"}`}
        </button>
        <button
          onClick={reject}
          disabled={status !== "idle"}
          className="px-3 py-1.5 rounded border border-white/20 text-xs disabled:opacity-40"
        >
          Reject all
        </button>
        <button
          onClick={() => setRawOpen((v) => !v)}
          className="px-3 py-1.5 rounded border border-white/10 text-xs text-white/60 hover:text-white"
        >
          {rawOpen ? "Hide raw" : "View raw JSON"}
        </button>
        {status === "applied" && (
          <span className="text-xs text-green-300">Written atomically. Journal updated.</span>
        )}
        {status === "rejected" && (
          <span className="text-xs text-white/50">Rejected. Nothing was written.</span>
        )}
      </div>

      {rawOpen && (
        <pre className="mt-2 text-[10px] bg-black/40 border border-white/10 rounded p-2 overflow-x-auto">
          {JSON.stringify(patch.patch, null, 2)}
        </pre>
      )}
    </div>
  );
}

function ValidationBadge({ state }: { state: ValidationState }) {
  if (state.kind === "ok") {
    return (
      <span className="text-[10px] uppercase tracking-wide bg-green-500/20 text-green-300 px-2 py-0.5 rounded">
        schema ok
      </span>
    );
  }
  if (state.kind === "error" || state.kind === "patch_failed") {
    return (
      <span className="text-[10px] uppercase tracking-wide bg-red-500/20 text-red-300 px-2 py-0.5 rounded">
        invalid
      </span>
    );
  }
  return (
    <span className="text-[10px] uppercase tracking-wide bg-white/10 text-white/50 px-2 py-0.5 rounded">
      checking…
    </span>
  );
}

function OpRow({
  op,
  before,
  checked,
  disabled,
  onToggle,
}: {
  op: Operation;
  before: unknown;
  checked: boolean;
  disabled: boolean;
  onToggle: (next: boolean) => void;
}) {
  const opName = op.op;
  const after = "value" in op ? op.value : undefined;
  const opColor: Record<string, string> = {
    add: "text-green-300",
    remove: "text-red-300",
    replace: "text-yellow-300",
    move: "text-blue-300",
    copy: "text-cyan-300",
    test: "text-white/60",
  };
  return (
    <label className="flex items-start gap-2 text-xs bg-black/20 border border-white/5 rounded p-2 cursor-pointer hover:border-white/20">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-0.5"
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className={`font-mono font-semibold ${opColor[opName] ?? "text-white"}`}>
            {opName}
          </span>
          <code className="text-white/70 font-mono truncate">{op.path}</code>
          {op.op === "move" && op.from && (
            <>
              <span className="text-white/40">from</span>
              <code className="text-white/70 font-mono truncate">{op.from}</code>
            </>
          )}
        </div>
        {op.op === "add" && (
          <div className="text-white/60">
            <span className="text-white/40">→ </span>
            <code className="text-green-300">{shortValue(after)}</code>
          </div>
        )}
        {op.op === "replace" && (
          <div className="text-white/60 space-y-0.5">
            <div>
              <span className="text-white/40">before: </span>
              <code className="text-white/70">{shortValue(before)}</code>
            </div>
            <div>
              <span className="text-white/40">after: </span>
              <code className="text-yellow-300">{shortValue(after)}</code>
            </div>
          </div>
        )}
        {op.op === "remove" && (
          <div className="text-white/60">
            <span className="text-white/40">removing: </span>
            <code className="text-red-300">{shortValue(before)}</code>
          </div>
        )}
      </div>
    </label>
  );
}
