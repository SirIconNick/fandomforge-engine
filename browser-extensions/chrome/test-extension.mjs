// Headless Playwright driver for the FandomForge Grab extension.
//
// Launches a persistent Chromium context with the extension loaded, then:
//   1. waits for the service worker to come up
//   2. reads its extension ID
//   3. opens popup.html in a tab (easier to drive than a real popup window)
//   4. verifies the project dropdown populates from the local dashboard
//   5. performs a grab POST and verifies the success path
//   6. exercises the options page
//
// Prereq: dashboard running on http://localhost:4321 (pnpm start in web/).
// Usage:
//   node browser-extensions/chrome/test-extension.mjs

import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { createRequire } from "node:module";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXTENSION_PATH = __dirname;

// playwright is installed via pnpm at the workspace root. Resolve its package
// directory, then require() it through a require instance rooted there — that
// way nested `playwright-core` gets resolved via the regular CJS lookup.
const repoRoot = path.resolve(__dirname, "..", "..");
const pnpmDir = path.join(repoRoot, "node_modules/.pnpm");
const pwGlob = fs
  .readdirSync(pnpmDir)
  .find((n) => n.startsWith("playwright@"));
if (!pwGlob) {
  console.error("playwright not installed. Run `pnpm install` in web/.");
  process.exit(1);
}
const playwrightPkg = path.join(pnpmDir, pwGlob, "node_modules/playwright");
const localRequire = createRequire(path.join(playwrightPkg, "index.js"));
const { chromium } = localRequire(path.join(playwrightPkg, "index.js"));
const DASHBOARD = process.env.FF_DASHBOARD ?? "http://localhost:4321";

const PASS = "PASS";
const FAIL = "FAIL";

let failures = 0;
function assert(cond, label) {
  if (cond) {
    console.log(`${PASS}: ${label}`);
  } else {
    console.error(`${FAIL}: ${label}`);
    failures += 1;
  }
}

async function run() {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "ff-ext-test-"));
  console.log(`profile: ${userDataDir}`);

  // Allow an explicit chromium binary via env (useful on sandboxed setups where
  // `playwright install` can't write to the cache dir).
  const launchOpts = {
    headless: true,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
      "--no-sandbox",
    ],
  };
  if (process.env.FF_CHROMIUM_BIN && fs.existsSync(process.env.FF_CHROMIUM_BIN)) {
    launchOpts.executablePath = process.env.FF_CHROMIUM_BIN;
  } else {
    // Auto-discover the newest playwright-installed chromium locally.
    const cacheDir = path.join(os.homedir(), "Library/Caches/ms-playwright");
    if (fs.existsSync(cacheDir)) {
      const dirs = fs
        .readdirSync(cacheDir)
        .filter((n) => n.startsWith("chromium-"))
        .sort();
      const latest = dirs[dirs.length - 1];
      if (latest) {
        const candidate = path.join(
          cacheDir,
          latest,
          "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        );
        if (fs.existsSync(candidate)) launchOpts.executablePath = candidate;
      }
    }
  }
  if (launchOpts.executablePath) {
    console.log(`using chromium: ${launchOpts.executablePath}`);
  }
  const context = await chromium.launchPersistentContext(userDataDir, launchOpts);

  let worker = context.serviceWorkers()[0];
  if (!worker) {
    worker = await context.waitForEvent("serviceworker", { timeout: 10000 });
  }
  const extensionId = new URL(worker.url()).host;
  console.log(`extension id: ${extensionId}`);
  assert(extensionId.length === 32, "extension id is 32 chars");

  // Pre-configure chrome.storage.sync so the popup/options have known defaults
  await worker.evaluate(
    async (dashboardUrl) => {
      await chrome.storage.sync.set({
        dashboardUrl,
        defaultProject: "grab-smoketest",
        defaultMode: "audio",
        defaultResolution: "480",
        defaultCookiesBrowser: "",
      });
    },
    DASHBOARD
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
    projectOptions.some((o) => o.value === "grab-smoketest"),
    "project dropdown contains grab-smoketest"
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

  await popup.fill("#url", "https://www.youtube.com/watch?v=jNQXAC9IVRw");
  await popup.fill("#note", "ext playwright smoke");
  await popup.click("#grab");

  await popup.waitForFunction(
    () => {
      const el = document.getElementById("status");
      return el && /grabbed|failed/i.test(el.textContent ?? "");
    },
    { timeout: 60000 }
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
  assert(optionsProjects.includes("grab-smoketest"), "options page lists grab-smoketest");
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

  if (failures > 0) {
    console.error(`\n${FAIL}: ${failures} assertion(s) failed.`);
    process.exit(1);
  }
  console.log(`\n${PASS}: all extension assertions passed.`);
}

run().catch((err) => {
  console.error(`${FAIL}: test threw: ${err.message}`);
  console.error(err.stack);
  process.exit(1);
});
