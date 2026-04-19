// Headless Playwright driver for the FandomForge Grab extension.
//
// Launches a persistent Chromium context with the extension loaded, then:
//   1. waits for the service worker to come up
//   2. reads its extension ID
//   3. opens popup.html in a tab (easier to drive than a real popup window)
//   4. verifies the project dropdown populates from the local dashboard
//   5. performs a grab POST and verifies the success path
//   6. exercises the options page + context menus
//
// Prereq: dashboard running on http://localhost:4321 (or FF_DASHBOARD).
// Usage:
//   node browser-extensions/chrome/test-extension.mjs
//
// Exit codes:
//   0 → all assertions passed
//   1 → assertion(s) failed
//   2 → skipped (prereqs missing: playwright / chromium / dashboard)

import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { createRequire } from "node:module";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXTENSION_PATH = __dirname;
const DASHBOARD = process.env.FF_DASHBOARD ?? "http://localhost:4321";
const STRICT = process.env.FF_EXT_STRICT === "1"; // fail instead of skip

const EXIT_OK = 0;
const EXIT_FAIL = 1;
const EXIT_SKIP = 2;

const PASS = "PASS";
const FAIL = "FAIL";
const SKIP = "SKIP";

let failures = 0;
function assert(cond, label) {
  if (cond) {
    console.log(`${PASS}: ${label}`);
  } else {
    console.error(`${FAIL}: ${label}`);
    failures += 1;
  }
}

function skipOrFail(reason) {
  if (STRICT) {
    console.error(`${FAIL}: ${reason}`);
    process.exit(EXIT_FAIL);
  }
  console.error(`${SKIP}: ${reason}`);
  process.exit(EXIT_SKIP);
}

// ---------- Prereq: resolve playwright ----------
const repoRoot = path.resolve(__dirname, "..", "..");
const pnpmDir = path.join(repoRoot, "node_modules/.pnpm");
if (!fs.existsSync(pnpmDir)) {
  skipOrFail(`node_modules/.pnpm not found at ${pnpmDir}. Run 'pnpm install' in web/.`);
}
const pwGlob = fs
  .readdirSync(pnpmDir)
  .find((n) => n.startsWith("playwright@"));
if (!pwGlob) {
  skipOrFail("playwright package not installed. Run 'pnpm install' in web/.");
}
const playwrightPkg = path.join(pnpmDir, pwGlob, "node_modules/playwright");
const playwrightEntry = path.join(playwrightPkg, "index.js");
if (!fs.existsSync(playwrightEntry)) {
  skipOrFail(`playwright entry missing at ${playwrightEntry}.`);
}
const localRequire = createRequire(playwrightEntry);
const { chromium } = localRequire(playwrightEntry);

// ---------- Prereq: locate a chromium binary ----------
function findChromium() {
  if (process.env.FF_CHROMIUM_BIN && fs.existsSync(process.env.FF_CHROMIUM_BIN)) {
    return process.env.FF_CHROMIUM_BIN;
  }
  const cacheDir = path.join(os.homedir(), "Library/Caches/ms-playwright");
  if (!fs.existsSync(cacheDir)) return null;
  const dirs = fs
    .readdirSync(cacheDir)
    .filter((n) => n.startsWith("chromium-"))
    .sort();
  for (const dir of [...dirs].reverse()) {
    const candidates = [
      path.join(cacheDir, dir, "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
      path.join(cacheDir, dir, "chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
      path.join(cacheDir, dir, "chrome-linux/chrome"),
      path.join(cacheDir, dir, "chrome-win/chrome.exe"),
    ];
    for (const c of candidates) {
      if (fs.existsSync(c)) return c;
    }
  }
  return null;
}

const chromiumBin = findChromium();
if (!chromiumBin) {
  skipOrFail(
    "No chromium binary found. Run 'pnpm --dir web exec playwright install chromium' " +
    "or set FF_CHROMIUM_BIN=/path/to/chromium."
  );
}

// ---------- Prereq: dashboard reachable ----------
async function dashboardReachable() {
  try {
    const res = await fetch(`${DASHBOARD}/api/projects`, {
      signal: AbortSignal.timeout(5000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

if (!(await dashboardReachable())) {
  skipOrFail(
    `Dashboard not reachable at ${DASHBOARD}/api/projects. ` +
    `Start it with 'pnpm --dir web build && pnpm --dir web start' or set FF_DASHBOARD.`
  );
}

// ---------- Verify the test project exists ----------
let projectsList = [];
try {
  const res = await fetch(`${DASHBOARD}/api/projects`);
  const data = await res.json();
  projectsList = data.projects ?? [];
} catch (err) {
  skipOrFail(`Could not list projects from dashboard: ${err.message}`);
}

const TEST_PROJECT = process.env.FF_EXT_PROJECT ?? "grab-smoketest";
const hasTestProject = projectsList.some((p) => p.slug === TEST_PROJECT);
if (!hasTestProject) {
  skipOrFail(
    `No test project '${TEST_PROJECT}' in the dashboard. ` +
    `Create one with 'ff project new ${TEST_PROJECT}' or set FF_EXT_PROJECT.`
  );
}

// ---------- Run the test ----------
async function run() {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "ff-ext-test-"));
  console.log(`profile: ${userDataDir}`);
  console.log(`chromium: ${chromiumBin}`);
  console.log(`dashboard: ${DASHBOARD}`);
  console.log(`project: ${TEST_PROJECT}`);

  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    executablePath: chromiumBin,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
      "--no-sandbox",
    ],
  });

  let worker = context.serviceWorkers()[0];
  if (!worker) {
    worker = await context.waitForEvent("serviceworker", { timeout: 10000 });
  }
  const extensionId = new URL(worker.url()).host;
  console.log(`extension id: ${extensionId}`);
  assert(extensionId.length === 32, "extension id is 32 chars");

  // Pre-configure chrome.storage.sync so the popup/options have known defaults
  await worker.evaluate(
    async ({ dashboardUrl, defaultProject }) => {
      await chrome.storage.sync.set({
        dashboardUrl,
        defaultProject,
        defaultMode: "audio",
        defaultResolution: "480",
        defaultCookiesBrowser: "",
      });
    },
    { dashboardUrl: DASHBOARD, defaultProject: TEST_PROJECT }
  );

  // ---------- Popup ----------
  const popup = await context.newPage();
  popup.on("console", (msg) => console.log(`  [popup ${msg.type()}] ${msg.text()}`));
  popup.on("pageerror", (err) => console.error(`  [popup error] ${err.message}`));

  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.waitForLoadState("domcontentloaded");

  await popup.waitForFunction(() => {
    const sel = document.getElementById("project");
    return sel && sel.options.length > 0;
  }, { timeout: 8000 });

  const projectOptions = await popup.evaluate(() =>
    Array.from(document.querySelectorAll("#project option")).map((o) => ({
      value: o.value,
      label: o.textContent,
    }))
  );
  console.log(`  projects: ${JSON.stringify(projectOptions)}`);
  assert(
    projectOptions.some((o) => o.value === TEST_PROJECT),
    `project dropdown contains ${TEST_PROJECT}`
  );

  const initialMode = await popup.evaluate(
    () => document.querySelector(".seg.active")?.dataset.mode
  );
  assert(initialMode === "audio", 'default mode is "audio" from stored settings');

  await popup.click('.seg[data-mode="video"]');
  const resDisabledVideo = await popup.evaluate(
    () => document.getElementById("resolution").disabled
  );
  assert(resDisabledVideo === false, "resolution enabled when mode != audio");

  await popup.click('.seg[data-mode="audio"]');
  const resDisabledAudio = await popup.evaluate(
    () => document.getElementById("resolution").disabled
  );
  assert(resDisabledAudio === true, "resolution disabled when mode == audio");

  // Short, stable, CC-licensed grab target: "Me at the zoo" (19s)
  const TEST_URL =
    process.env.FF_EXT_URL ?? "https://www.youtube.com/watch?v=jNQXAC9IVRw";
  await popup.fill("#url", TEST_URL);
  await popup.fill("#note", "ext playwright smoke");
  await popup.click("#grab");

  await popup.waitForFunction(
    () => {
      const el = document.getElementById("status");
      return el && /grabbed|failed/i.test(el.textContent ?? "");
    },
    { timeout: 120000 }
  );
  const status = await popup.evaluate(() => ({
    text: document.getElementById("status").textContent,
    kind: document.getElementById("status").className,
  }));
  console.log(`  popup status: ${JSON.stringify(status)}`);
  assert(/grabbed/i.test(status.text), "popup reports grab success");
  assert(status.kind.includes("ok"), "status class marks success");

  // ---------- Options page ----------
  const options = await context.newPage();
  options.on("pageerror", (err) => console.error(`  [options error] ${err.message}`));
  await options.goto(`chrome-extension://${extensionId}/options.html`);
  await options.waitForLoadState("domcontentloaded");

  await options.waitForFunction(() => {
    const sel = document.getElementById("default-project");
    return sel && sel.options.length >= 1;
  }, { timeout: 8000 });

  const optionsProjects = await options.evaluate(() =>
    Array.from(document.querySelectorAll("#default-project option")).map((o) => o.value)
  );
  assert(optionsProjects.includes(TEST_PROJECT), `options page lists ${TEST_PROJECT}`);
  assert(optionsProjects.includes(""), "options page has blank 'prompt each time' entry");

  // ---------- Context menu registration ----------
  const menusRegistered = await worker.evaluate(
    () =>
      new Promise((resolve) => {
        chrome.contextMenus.update(
          "ff-grab-link",
          { title: "Grab to FandomForge" },
          () => resolve(!chrome.runtime.lastError)
        );
      })
  );
  assert(menusRegistered === true, "ff-grab-link context menu is registered");

  const pageMenuRegistered = await worker.evaluate(
    () =>
      new Promise((resolve) => {
        chrome.contextMenus.update("ff-grab-page", {}, () =>
          resolve(!chrome.runtime.lastError)
        );
      })
  );
  assert(pageMenuRegistered === true, "ff-grab-page context menu is registered");

  await context.close();
}

try {
  await run();
} catch (err) {
  console.error(`${FAIL}: test threw: ${err.message}`);
  console.error(err.stack);
  process.exit(EXIT_FAIL);
}

if (failures > 0) {
  console.error(`\n${FAIL}: ${failures} assertion(s) failed.`);
  process.exit(EXIT_FAIL);
}
console.log(`\n${PASS}: all extension assertions passed.`);
process.exit(EXIT_OK);
