"use client";

import { useEffect, useMemo, useState } from "react";
import { compare } from "fast-json-patch";
import SchemaForm from "@/components/SchemaForm";

export interface ArtifactEditorProps {
  projectSlug: string;
  artifactType: string;
  seed?: unknown;
  title?: string;
  helpText?: string;
}

interface SchemaError {
  instancePath?: string;
  message?: string;
  keyword?: string;
}

interface LoadState {
  loading: boolean;
  exists: boolean;
  sha256: string | null;
  error: string | null;
}

type ViewMode = "form" | "json";

export default function ArtifactEditor({
  projectSlug,
  artifactType,
  seed,
  title,
  helpText,
}: ArtifactEditorProps) {
  const [text, setText] = useState("");
  const [initial, setInitial] = useState("");
  const [load, setLoad] = useState<LoadState>({
    loading: true,
    exists: false,
    sha256: null,
    error: null,
  });
  const [parseError, setParseError] = useState<string | null>(null);
  const [schemaErrors, setSchemaErrors] = useState<SchemaError[] | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">(
    "idle"
  );
  const [saveError, setSaveError] = useState("");
  const [schema, setSchema] = useState<Record<string, unknown> | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("form");

  const dirty = text !== initial;

  useEffect(() => {
    const url = `/api/artifacts/schema?artifact=${encodeURIComponent(artifactType)}`;
    fetch(url)
      .then((r) => r.json())
      .then((d: { ok?: boolean; schema?: Record<string, unknown> }) => {
        if (d.ok && d.schema) setSchema(d.schema);
      })
      .catch(() => {
        /* schema optional — falls back to JSON-only editing */
      });
  }, [artifactType]);

  useEffect(() => {
    const url = `/api/artifacts/read?project=${encodeURIComponent(projectSlug)}&artifact=${encodeURIComponent(artifactType)}`;
    fetch(url)
      .then((r) => r.json())
      .then(
        (d: {
          exists: boolean;
          data: unknown;
          sha256: string | null;
          error?: string;
        }) => {
          if (d.error) {
            setLoad({ loading: false, exists: false, sha256: null, error: d.error });
            return;
          }
          const seedJson =
            !d.exists && seed !== undefined
              ? JSON.stringify(seed, null, 2) + "\n"
              : "";
          const initialJson = d.exists
            ? JSON.stringify(d.data, null, 2) + "\n"
            : seedJson;
          setText(initialJson);
          setInitial(initialJson);
          setLoad({
            loading: false,
            exists: d.exists,
            sha256: d.sha256,
            error: null,
          });
        }
      )
      .catch((e) =>
        setLoad({
          loading: false,
          exists: false,
          sha256: null,
          error: (e as Error).message,
        })
      );
  }, [projectSlug, artifactType, seed]);

  const parsed: { ok: boolean; data: unknown } = useMemo(() => {
    if (!text.trim()) return { ok: true, data: null };
    try {
      return { ok: true, data: JSON.parse(text) };
    } catch (e) {
      setParseError((e as Error).message);
      return { ok: false, data: null };
    }
  }, [text]);

  useEffect(() => {
    if (parsed.ok) setParseError(null);
  }, [parsed.ok]);

  useEffect(() => {
    if (!parsed.ok || parsed.data == null) {
      setSchemaErrors(null);
      return;
    }
    const handle = setTimeout(() => {
      fetch("/api/artifacts/validate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ artifact: artifactType, data: parsed.data }),
      })
        .then((r) => r.json())
        .then((d: { ok: boolean; errors: SchemaError[] | null }) => {
          setSchemaErrors(d.ok ? null : d.errors ?? []);
        })
        .catch(() => setSchemaErrors(null));
    }, 400);
    return () => clearTimeout(handle);
  }, [parsed.ok, parsed.data, artifactType]);

  async function save() {
    if (!parsed.ok || parsed.data == null) {
      setSaveError("Cannot save while JSON is invalid.");
      setSaveState("error");
      return;
    }
    if (schemaErrors && schemaErrors.length > 0) {
      setSaveError("Cannot save while schema validation is failing.");
      setSaveState("error");
      return;
    }

    setSaveState("saving");
    setSaveError("");

    const previous = (() => {
      if (!initial.trim()) return {};
      try {
        return JSON.parse(initial);
      } catch {
        return {};
      }
    })();
    const patch = compare(previous, parsed.data as object);

    if (patch.length === 0) {
      setSaveState("saved");
      return;
    }

    try {
      const res = await fetch("/api/artifacts/apply", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_slug: projectSlug,
          artifact: artifactType,
          patch,
          expected_sha256: load.sha256,
          rationale: "manual edit via ArtifactEditor UI",
          expert_slug: null,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setSaveState("error");
        setSaveError(
          body.error ??
            `save failed (${res.status})` +
              (body.schema_errors ? ` — ${JSON.stringify(body.schema_errors)}` : "")
        );
        return;
      }
      setSaveState("saved");
      setInitial(text);
      setLoad((prev) => ({ ...prev, exists: true, sha256: body.after_sha256 }));
    } catch (e) {
      setSaveState("error");
      setSaveError((e as Error).message);
    }
  }

  async function rollback() {
    if (!confirm("Roll back the last applied change?")) return;
    const res = await fetch("/api/artifacts/rollback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        project_slug: projectSlug,
        artifact: artifactType,
        steps: 1,
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      alert(body.error ?? `rollback failed (${res.status})`);
      return;
    }
    const fresh = await fetch(
      `/api/artifacts/read?project=${encodeURIComponent(projectSlug)}&artifact=${encodeURIComponent(artifactType)}`
    ).then((r) => r.json());
    const initialJson = fresh.exists
      ? JSON.stringify(fresh.data, null, 2) + "\n"
      : "";
    setText(initialJson);
    setInitial(initialJson);
    setLoad({
      loading: false,
      exists: fresh.exists,
      sha256: fresh.sha256,
      error: null,
    });
    setSaveState("idle");
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-semibold">{title ?? artifactType}</h2>
          {helpText && <p className="text-xs text-white/50 mt-1">{helpText}</p>}
        </div>
        <div className="flex items-center gap-2 text-xs">
          {load.exists ? (
            <span className="px-2 py-0.5 rounded bg-green-500/15 text-green-300 border border-green-500/30">
              on disk
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded bg-yellow-500/15 text-yellow-300 border border-yellow-500/30">
              new
            </span>
          )}
          {dirty && (
            <span className="px-2 py-0.5 rounded bg-[var(--color-forge,#ff5a1f)]/15 text-[var(--color-forge,#ff5a1f)] border border-[var(--color-forge,#ff5a1f)]/40">
              unsaved
            </span>
          )}
          {parseError ? (
            <span className="px-2 py-0.5 rounded bg-red-500/15 text-red-300 border border-red-500/30">
              JSON invalid
            </span>
          ) : schemaErrors && schemaErrors.length > 0 ? (
            <span className="px-2 py-0.5 rounded bg-red-500/15 text-red-300 border border-red-500/30">
              schema invalid
            </span>
          ) : parsed.ok ? (
            <span className="px-2 py-0.5 rounded bg-green-500/15 text-green-300 border border-green-500/30">
              valid
            </span>
          ) : null}
        </div>
      </div>

      {schema && (
        <div
          role="tablist"
          aria-label="Editor view mode"
          className="inline-flex border border-white/10 rounded text-xs"
        >
          <button
            role="tab"
            aria-selected={viewMode === "form"}
            onClick={() => setViewMode("form")}
            className={`px-3 py-1 ${viewMode === "form" ? "bg-white/10" : ""}`}
          >
            Form
          </button>
          <button
            role="tab"
            aria-selected={viewMode === "json"}
            onClick={() => setViewMode("json")}
            className={`px-3 py-1 border-l border-white/10 ${
              viewMode === "json" ? "bg-white/10" : ""
            }`}
          >
            JSON
          </button>
        </div>
      )}

      {load.loading ? (
        <div className="text-white/50 text-sm">Loading…</div>
      ) : load.error ? (
        <div className="text-red-300 text-sm bg-red-500/10 border border-red-500/30 rounded p-3">
          {load.error}
        </div>
      ) : (
        <>
          {schema && viewMode === "form" && parsed.ok && parsed.data && typeof parsed.data === "object" ? (
            <div className="bg-black/20 border border-white/10 rounded p-3 max-h-[600px] overflow-y-auto">
              <SchemaForm
                schema={schema}
                value={parsed.data}
                onChange={(next) => {
                  try {
                    const serialized = JSON.stringify(next, null, 2) + "\n";
                    setText(serialized);
                    if (saveState === "saved") setSaveState("idle");
                  } catch {
                    /* ignore serialization failures */
                  }
                }}
              />
            </div>
          ) : null}
          {(!schema || viewMode === "json" || !parsed.ok || !parsed.data) && (
            <textarea
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                if (saveState === "saved") setSaveState("idle");
              }}
              className="w-full min-h-[400px] font-mono text-xs bg-black/40 border border-white/10 rounded p-3 resize-y"
              spellCheck={false}
            />
          )}

          {parseError && (
            <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">
              JSON parse error: {parseError}
            </div>
          )}

          {schemaErrors && schemaErrors.length > 0 && (
            <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">
              <div className="font-semibold mb-1">Schema errors:</div>
              <ul className="list-disc ml-4 space-y-0.5">
                {schemaErrors.slice(0, 8).map((e, i) => (
                  <li key={i}>
                    <code className="text-red-200">{e.instancePath || "/"}</code>{" "}
                    {e.message ?? e.keyword}
                  </li>
                ))}
                {schemaErrors.length > 8 && (
                  <li className="text-white/50">
                    +{schemaErrors.length - 8} more
                  </li>
                )}
              </ul>
            </div>
          )}

          {saveError && (
            <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 whitespace-pre-wrap">
              {saveError}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={save}
              disabled={
                saveState === "saving" ||
                !parsed.ok ||
                !!parseError ||
                (schemaErrors && schemaErrors.length > 0) ||
                !dirty
              }
              className="px-3 py-1.5 rounded bg-[var(--color-forge,#ff5a1f)] text-black font-semibold text-xs disabled:opacity-40"
            >
              {saveState === "saving"
                ? "Saving…"
                : saveState === "saved"
                  ? "Saved"
                  : "Save"}
            </button>
            {load.exists && (
              <button
                onClick={rollback}
                className="px-3 py-1.5 rounded border border-white/20 text-xs"
              >
                Undo last change
              </button>
            )}
            <span className="text-[10px] text-white/40 ml-auto">
              sha256: {load.sha256 ? load.sha256.slice(0, 12) : "—"}
            </span>
          </div>
        </>
      )}
    </section>
  );
}
