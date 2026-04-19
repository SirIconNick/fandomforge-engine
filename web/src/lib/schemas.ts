import { promises as fs } from "node:fs";
import path from "node:path";
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { PROJECT_ROOT } from "@/lib/fs";

export type ArtifactType =
  | "edit-plan"
  | "beat-map"
  | "shot-list"
  | "color-plan"
  | "transition-plan"
  | "audio-plan"
  | "title-plan"
  | "qa-report"
  | "fandoms"
  | "emotion-arc"
  | "post-render-review"
  | "sync-plan"
  | "sfx-plan"
  | "complement-plan"
  | "reference-priors";

export const EXPECTED_SCHEMA_VERSIONS: Record<ArtifactType, number> = {
  "edit-plan": 1,
  "beat-map": 1,
  "shot-list": 1,
  "color-plan": 1,
  "transition-plan": 1,
  "audio-plan": 1,
  "title-plan": 1,
  "qa-report": 1,
  "fandoms": 1,
  "emotion-arc": 1,
  "post-render-review": 1,
  "sync-plan": 1,
  "sfx-plan": 1,
  "complement-plan": 1,
  "reference-priors": 1,
};

const SCHEMAS_DIR = path.join(PROJECT_ROOT, "tools", "fandomforge", "schemas");

interface SchemaBundle {
  ajv: Ajv2020;
  validators: Map<ArtifactType, ValidateFunction>;
}

let bundlePromise: Promise<SchemaBundle> | null = null;

async function loadBundle(): Promise<SchemaBundle> {
  const ajv = new Ajv2020({
    allErrors: true,
    strict: false,
    allowUnionTypes: true,
  });
  addFormats(ajv);

  const files: Array<[ArtifactType, string]> = [
    ["edit-plan", "edit-plan.schema.json"],
    ["beat-map", "beat-map.schema.json"],
    ["shot-list", "shot-list.schema.json"],
    ["color-plan", "color-plan.schema.json"],
    ["transition-plan", "transition-plan.schema.json"],
    ["audio-plan", "audio-plan.schema.json"],
    ["title-plan", "title-plan.schema.json"],
    ["qa-report", "qa-report.schema.json"],
    ["fandoms", "fandoms.schema.json"],
    ["emotion-arc", "emotion-arc.schema.json"],
    ["post-render-review", "post-render-review.schema.json"],
    ["sync-plan", "sync-plan.schema.json"],
    ["sfx-plan", "sfx-plan.schema.json"],
    ["complement-plan", "complement-plan.schema.json"],
    ["reference-priors", "reference-priors.schema.json"],
  ];

  const validators = new Map<ArtifactType, ValidateFunction>();
  for (const [id, filename] of files) {
    const p = path.join(SCHEMAS_DIR, filename);
    try {
      const raw = await fs.readFile(p, "utf8");
      const schema = JSON.parse(raw) as Record<string, unknown>;
      const validate = ajv.compile(schema);
      validators.set(id, validate);
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw new Error(
        `Failed to compile schema ${filename}: ${(err as Error).message}`
      );
    }
  }

  return { ajv, validators };
}

function getBundle(): Promise<SchemaBundle> {
  if (!bundlePromise) {
    bundlePromise = loadBundle();
  }
  return bundlePromise;
}

export interface ValidationResult {
  ok: boolean;
  errors: ErrorObject[] | null;
}

export async function validateArtifact(
  artifact: ArtifactType,
  data: unknown
): Promise<ValidationResult> {
  const { validators } = await getBundle();
  const validate = validators.get(artifact);
  if (!validate) {
    return {
      ok: false,
      errors: [
        {
          instancePath: "",
          schemaPath: "",
          keyword: "missing-schema",
          params: { artifact },
          message: `No schema registered for artifact type '${artifact}'`,
        } as ErrorObject,
      ],
    };
  }
  const expectedVersion = EXPECTED_SCHEMA_VERSIONS[artifact];
  if (
    expectedVersion !== undefined &&
    typeof data === "object" &&
    data !== null &&
    "schema_version" in data
  ) {
    const actual = (data as { schema_version?: unknown }).schema_version;
    if (typeof actual === "number" && actual !== expectedVersion) {
      return {
        ok: false,
        errors: [
          {
            instancePath: "/schema_version",
            schemaPath: "#/properties/schema_version",
            keyword: "schema-version-mismatch",
            params: { artifact, expected: expectedVersion, actual },
            message: `schema_version ${actual} does not match expected ${expectedVersion} for '${artifact}'`,
          } as ErrorObject,
        ],
      };
    }
  }
  const ok = validate(data);
  return { ok, errors: ok ? null : validate.errors ?? null };
}

export async function listRegisteredArtifacts(): Promise<ArtifactType[]> {
  const { validators } = await getBundle();
  return Array.from(validators.keys());
}
