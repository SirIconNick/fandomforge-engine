"use client";

import { useMemo } from "react";

type JSONValue = unknown;

interface Schema {
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema;
  additionalProperties?: boolean | Schema;
  description?: string;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  format?: string;
  default?: unknown;
  $ref?: string;
  $defs?: Record<string, Schema>;
  anyOf?: Schema[];
  oneOf?: Schema[];
}

export interface SchemaFormProps {
  schema: Schema;
  value: JSONValue;
  onChange: (next: JSONValue) => void;
  disabled?: boolean;
}

function resolveRef(root: Schema, ref: string): Schema | null {
  if (!ref.startsWith("#/")) return null;
  const parts = ref.slice(2).split("/");
  let cur: unknown = root;
  for (const p of parts) {
    if (cur && typeof cur === "object" && p in (cur as object)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return null;
    }
  }
  return (cur as Schema) ?? null;
}

function resolve(root: Schema, schema: Schema | undefined): Schema {
  if (!schema) return {};
  if (schema.$ref) {
    const r = resolveRef(root, schema.$ref);
    return r ?? schema;
  }
  return schema;
}

function schemaType(schema: Schema): string {
  if (Array.isArray(schema.type)) return schema.type.find((t) => t !== "null") ?? "any";
  return (schema.type as string) ?? (schema.enum ? "enum" : "any");
}

function defaultForSchema(root: Schema, schema: Schema): JSONValue {
  const resolved = resolve(root, schema);
  if (resolved.default !== undefined) return resolved.default;
  if (resolved.enum && resolved.enum.length > 0) return resolved.enum[0];
  const t = schemaType(resolved);
  switch (t) {
    case "string":
      return "";
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    case "array":
      return [];
    case "object":
      return {};
    default:
      return null;
  }
}

function FieldLabel({
  name,
  required,
  description,
}: {
  name: string;
  required: boolean;
  description?: string;
}) {
  return (
    <div className="mb-1">
      <span className="text-xs font-semibold text-white/80">
        {name}
        {required && <span className="text-[var(--color-forge,#ff5a1f)] ml-0.5">*</span>}
      </span>
      {description && (
        <div className="text-[10px] text-white/50 mt-0.5">{description}</div>
      )}
    </div>
  );
}

function PrimitiveInput({
  schema,
  value,
  onChange,
  disabled,
}: {
  schema: Schema;
  value: JSONValue;
  onChange: (v: JSONValue) => void;
  disabled?: boolean;
}) {
  const t = schemaType(schema);

  if (schema.enum) {
    return (
      <select
        disabled={disabled}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5 text-xs"
      >
        {schema.enum.map((v, i) => (
          <option key={i} value={String(v)}>
            {String(v)}
          </option>
        ))}
      </select>
    );
  }

  if (t === "boolean") {
    return (
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          disabled={disabled}
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="text-white/60">{Boolean(value) ? "true" : "false"}</span>
      </label>
    );
  }

  if (t === "integer" || t === "number") {
    return (
      <input
        type="number"
        disabled={disabled}
        value={value == null ? "" : String(value)}
        step={t === "integer" ? 1 : "any"}
        min={schema.minimum}
        max={schema.maximum}
        onChange={(e) => {
          if (e.target.value === "") {
            onChange(null);
            return;
          }
          const parsed =
            t === "integer" ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
          onChange(Number.isFinite(parsed) ? parsed : value);
        }}
        className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5 text-xs font-mono"
      />
    );
  }

  const useTextarea =
    t === "string" && ((schema.maxLength ?? 0) > 200 || schema.format === "markdown");
  if (useTextarea) {
    return (
      <textarea
        disabled={disabled}
        value={value == null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5 text-xs min-h-16 resize-y"
      />
    );
  }

  return (
    <input
      type="text"
      disabled={disabled}
      value={value == null ? "" : String(value)}
      onChange={(e) => onChange(e.target.value)}
      placeholder={schema.pattern ? `pattern: ${schema.pattern}` : undefined}
      className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5 text-xs"
    />
  );
}

function ArrayField({
  root,
  schema,
  value,
  onChange,
  disabled,
}: {
  root: Schema;
  schema: Schema;
  value: JSONValue;
  onChange: (v: JSONValue) => void;
  disabled?: boolean;
}) {
  const items = Array.isArray(value) ? value : [];
  const itemSchema = resolve(root, schema.items);
  return (
    <div className="space-y-2 pl-2 border-l border-white/10">
      {items.map((item, i) => (
        <div key={i} className="flex items-start gap-2">
          <div className="flex-1">
            <SchemaFormField
              root={root}
              schema={itemSchema}
              value={item}
              onChange={(next) => {
                const copy = items.slice();
                copy[i] = next;
                onChange(copy);
              }}
              disabled={disabled}
            />
          </div>
          <button
            type="button"
            onClick={() => {
              const copy = items.slice();
              copy.splice(i, 1);
              onChange(copy);
            }}
            disabled={disabled}
            className="mt-1 text-[10px] px-2 py-0.5 rounded border border-red-500/40 text-red-300 hover:bg-red-500/10 disabled:opacity-40"
            aria-label={`Remove item ${i + 1}`}
          >
            remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, defaultForSchema(root, itemSchema)])}
        disabled={disabled}
        className="text-[10px] px-2 py-1 rounded border border-white/20 hover:border-[var(--color-forge,#ff5a1f)]/60 disabled:opacity-40"
      >
        + add {items.length === 0 ? "first item" : "item"}
      </button>
    </div>
  );
}

function ObjectField({
  root,
  schema,
  value,
  onChange,
  disabled,
}: {
  root: Schema;
  schema: Schema;
  value: JSONValue;
  onChange: (v: JSONValue) => void;
  disabled?: boolean;
}) {
  const obj = (value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {}) as Record<string, JSONValue>;
  const props = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const keys = Object.keys(props);
  return (
    <div className="space-y-3">
      {keys.map((k) => {
        const fieldSchema = resolve(root, props[k]);
        return (
          <div key={k}>
            <FieldLabel
              name={k}
              required={required.has(k)}
              description={fieldSchema.description}
            />
            <SchemaFormField
              root={root}
              schema={fieldSchema}
              value={obj[k] ?? (required.has(k) ? undefined : obj[k])}
              onChange={(nv) => {
                const copy = { ...obj };
                if (nv === undefined) {
                  delete copy[k];
                } else {
                  copy[k] = nv;
                }
                onChange(copy);
              }}
              disabled={disabled}
            />
          </div>
        );
      })}
    </div>
  );
}

function SchemaFormField({
  root,
  schema,
  value,
  onChange,
  disabled,
}: {
  root: Schema;
  schema: Schema;
  value: JSONValue;
  onChange: (v: JSONValue) => void;
  disabled?: boolean;
}) {
  const resolved = resolve(root, schema);
  const t = schemaType(resolved);

  if (resolved.anyOf || resolved.oneOf) {
    return (
      <JsonFallback value={value} onChange={onChange} disabled={disabled}>
        <span className="text-[10px] text-white/40">
          {resolved.anyOf ? "anyOf" : "oneOf"} — edit as JSON
        </span>
      </JsonFallback>
    );
  }

  switch (t) {
    case "object":
      if (resolved.additionalProperties && !resolved.properties) {
        return (
          <JsonFallback value={value} onChange={onChange} disabled={disabled}>
            <span className="text-[10px] text-white/40">
              map type (additionalProperties) — edit as JSON
            </span>
          </JsonFallback>
        );
      }
      return (
        <ObjectField
          root={root}
          schema={resolved}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      );
    case "array":
      return (
        <ArrayField
          root={root}
          schema={resolved}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      );
    default:
      return (
        <PrimitiveInput
          schema={resolved}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      );
  }
}

function JsonFallback({
  value,
  onChange,
  disabled,
  children,
}: {
  value: JSONValue;
  onChange: (v: JSONValue) => void;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      {children}
      <textarea
        disabled={disabled}
        defaultValue={JSON.stringify(value ?? null, null, 2)}
        onBlur={(e) => {
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            /* keep prior value on parse fail */
          }
        }}
        className="w-full bg-black/30 border border-white/10 rounded px-2 py-1.5 text-[10px] font-mono min-h-16 resize-y"
      />
    </div>
  );
}

export default function SchemaForm({
  schema,
  value,
  onChange,
  disabled,
}: SchemaFormProps) {
  const root = useMemo(() => schema, [schema]);
  return (
    <SchemaFormField
      root={root}
      schema={schema}
      value={value}
      onChange={onChange}
      disabled={disabled}
    />
  );
}
