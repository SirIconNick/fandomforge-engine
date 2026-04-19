import { expect, test } from "@playwright/test";
import { promises as fs } from "node:fs";
import path from "node:path";

/**
 * End-to-end: seed a project with a passing qa-report.json, visit the QA
 * panel, and confirm it renders the rules + summary. Then write a failing
 * qa-report and confirm fail + override input appear.
 */

const PROJECT_ROOT = path.resolve(__dirname, "..", "..", "..");
const TEST_SLUG = `e2e-qa-${Date.now().toString(36)}`;
const TEST_PROJECT_PATH = path.join(PROJECT_ROOT, "projects", TEST_SLUG);

async function writeReport(status: "pass" | "fail" | "warn", extraRule?: Record<string, unknown>) {
  const report = {
    schema_version: 1,
    project_slug: TEST_SLUG,
    stage: "pre-export",
    status,
    rules: [
      {
        id: "qa.refs",
        name: "Unresolved references",
        level: "block",
        status: status === "fail" ? "fail" : "pass",
        message:
          status === "fail" ? "2 shots reference sources not in the catalog" : "all shots resolve",
      },
      {
        id: "qa.duration",
        name: "Duration math",
        level: "block",
        status: "pass",
      },
      ...(extraRule ? [extraRule] : []),
    ],
    summary: {
      total: 2 + (extraRule ? 1 : 0),
      passed: status === "fail" ? 1 : 2,
      warned: 0,
      failed: status === "fail" ? 1 : 0,
      overridden: 0,
    },
  };
  const dataDir = path.join(TEST_PROJECT_PATH, "data");
  await fs.mkdir(dataDir, { recursive: true });
  await fs.writeFile(
    path.join(dataDir, "qa-report.json"),
    JSON.stringify(report, null, 2)
  );
}

test.beforeAll(async () => {
  await fs.mkdir(TEST_PROJECT_PATH, { recursive: true });
});

test.afterAll(async () => {
  await fs.rm(TEST_PROJECT_PATH, { recursive: true, force: true });
});

test("QA panel renders a passing report", async ({ page }) => {
  await writeReport("pass");
  await page.goto(`/projects/${TEST_SLUG}/qa`);
  await expect(page.getByRole("heading", { name: `QA gate — ${TEST_SLUG}` })).toBeVisible();
  await expect(page.getByText("PASS", { exact: true })).toBeVisible();
  await expect(page.getByText("Unresolved references")).toBeVisible();
  await expect(page.getByText("Duration math")).toBeVisible();
});

test("QA panel surfaces failing rules with override input", async ({ page }) => {
  await writeReport("fail");
  await page.goto(`/projects/${TEST_SLUG}/qa`);
  await expect(page.getByText("FAIL", { exact: true })).toBeVisible();
  await expect(page.getByText(/2 shots reference sources/)).toBeVisible();
  await expect(page.getByPlaceholder(/reason for override/i)).toBeVisible();
});
