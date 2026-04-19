const DEFAULT_SETTINGS = {
  dashboardUrl: "http://localhost:4321",
  defaultProject: "",
  defaultMode: "both",
  defaultResolution: "1080",
  defaultCookiesBrowser: "",
};

const $ = (id) => document.getElementById(id);

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function appendOption(select, value, label, selected = false) {
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = label;
  if (selected) opt.selected = true;
  select.appendChild(opt);
}

function setStatus(text, kind = "info") {
  const el = $("status");
  el.textContent = text;
  el.className = `status ${kind}`;
}

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

async function populateProjects(settings) {
  const select = $("default-project");
  clearChildren(select);
  appendOption(select, "", "— prompt each time —", !settings.defaultProject);

  try {
    const base = settings.dashboardUrl.replace(/\/+$/, "");
    const res = await fetch(`${base}/api/projects`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const projects = Array.isArray(data.projects) ? data.projects : [];
    for (const p of projects) {
      appendOption(select, p.slug, p.name || p.slug, p.slug === settings.defaultProject);
    }
  } catch (err) {
    setStatus(
      `can't reach dashboard at ${settings.dashboardUrl} — ${err instanceof Error ? err.message : err}`,
      "error"
    );
  }
}

async function saveSettings() {
  const next = {
    dashboardUrl: $("dashboard-url").value.trim() || DEFAULT_SETTINGS.dashboardUrl,
    defaultProject: $("default-project").value,
    defaultMode: $("default-mode").value,
    defaultResolution: $("default-resolution").value,
    defaultCookiesBrowser: $("default-cookies-browser").value,
  };
  await chrome.storage.sync.set(next);

  // Also request optional host permission for the configured dashboard so the
  // service worker can fetch even on non-localhost URLs.
  try {
    const origin = new URL(next.dashboardUrl).origin + "/*";
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) {
      setStatus("saved (but host permission denied — dashboard must be on localhost)", "info");
      return;
    }
  } catch {
    // URL invalid; fall through
  }
  setStatus("saved ✓", "ok");
}

document.addEventListener("DOMContentLoaded", async () => {
  const settings = await loadSettings();
  $("dashboard-url").value = settings.dashboardUrl;
  $("default-mode").value = settings.defaultMode;
  $("default-resolution").value = settings.defaultResolution;
  $("default-cookies-browser").value = settings.defaultCookiesBrowser;
  await populateProjects(settings);
  $("save").addEventListener("click", saveSettings);
  $("dashboard-url").addEventListener("blur", async () => {
    const next = { ...settings, dashboardUrl: $("dashboard-url").value.trim() };
    await populateProjects(next);
  });
});
