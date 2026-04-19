import { expect, test } from "@playwright/test";
import { promises as fs } from "node:fs";
import path from "node:path";

/**
 * End-to-end: step through the new-project wizard, create a project,
 * land on the detail page, and upload a file through the dropzone.
 *
 * The project folder is cleaned up in afterAll so re-runs stay idempotent.
 */

const PROJECT_ROOT = path.resolve(__dirname, "..", "..", "..");
const TEST_SLUG = `e2e-wizard-${Date.now().toString(36)}`;
const TEST_PROJECT_PATH = path.join(PROJECT_ROOT, "projects", TEST_SLUG);

test.afterAll(async () => {
  await fs.rm(TEST_PROJECT_PATH, { recursive: true, force: true });
});

test("wizard creates a project and upload dropzone accepts a file", async ({ page }) => {
  await page.goto("/projects/new");

  // Step 1: slug + theme.
  await expect(page.getByRole("heading", { name: "New project" })).toBeVisible();
  await page.getByPlaceholder("mentor-loss-multifandom").fill(TEST_SLUG);
  await page
    .getByPlaceholder("Every mentor who saw the fall coming and stayed anyway.")
    .fill("End-to-end playwright wizard test");
  await page.getByRole("button", { name: "Next" }).click();

  // Step 2: song.
  await expect(page.getByRole("heading", { name: "Song" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();

  // Step 3: fandoms default content is fine.
  await expect(page.getByRole("heading", { name: "Fandoms" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();

  // Step 4: vibe + length defaults.
  await expect(page.getByRole("heading", { name: "Vibe and length" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();

  // Step 5: platform + submit.
  await expect(page.getByRole("heading", { name: "Platform" })).toBeVisible();
  await page.getByRole("button", { name: "Create project" }).click();

  // Lands on the project detail page.
  await page.waitForURL(new RegExp(`/projects/${TEST_SLUG}`));
  await expect(page.getByText("Upload source media")).toBeVisible();
  await expect(page.getByText("Assets")).toBeVisible();

  // edit-plan.json should exist on disk.
  const editPlan = path.join(TEST_PROJECT_PATH, "data", "edit-plan.json");
  const editPlanExists = await fs
    .access(editPlan)
    .then(() => true)
    .catch(() => false);
  expect(editPlanExists).toBe(true);

  // Upload a small file through the dropzone input.
  const fileInput = page.locator('input[type="file"]');
  const tmpFile = path.join(__dirname, "upload-fixture.srt");
  await fs.writeFile(tmpFile, "1\n00:00:00,000 --> 00:00:02,000\nhello\n");
  await fileInput.setInputFiles(tmpFile);

  await expect(page.getByText(/Saved/i)).toBeVisible();
  await expect(page.getByText("upload-fixture.srt")).toBeVisible();

  const rawFile = path.join(TEST_PROJECT_PATH, "raw", "upload-fixture.srt");
  const rawExists = await fs
    .access(rawFile)
    .then(() => true)
    .catch(() => false);
  expect(rawExists).toBe(true);

  await fs.rm(tmpFile, { force: true });
});
