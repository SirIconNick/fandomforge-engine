const DEFAULT_SETTINGS = {
  dashboardUrl: "http://localhost:4321",
  defaultProject: "",
  defaultMode: "both",
  defaultResolution: "1080",
  defaultCookiesBrowser: "",
};

const $ = (id) => document.getElementById(id);

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

function setStatus(text, kind = "info") {
  const el = $("status");
  el.textContent = text;
  el.className = `status ${kind}`;
}

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

function getActiveMode() {
  const active = document.querySelector(".seg.active");
  return active?.dataset.mode ?? "both";
}

async function fetchProjects(dashboardUrl) {
  const base = dashboardUrl.replace(/\/+$/, "");
  const res = await fetch(`${base}/api/projects`);
  if (!res.ok) throw new Error(`projects list: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data.projects) ? data.projects : [];
}

async function populateProjects(settings) {
  const select = $("project");
  clearChildren(select);
  try {
    const projects = await fetchProjects(settings.dashboardUrl);
    if (projects.length === 0) {
      appendOption(select, "", "no projects — create one in the dashboard");
      return;
    }
    for (const p of projects) {
      appendOption(select, p.slug, p.name || p.slug, p.slug === settings.defaultProject);
    }
  } catch (_err) {
    appendOption(select, "", `dashboard offline? (${settings.dashboardUrl})`);
    setStatus(`can't reach ${settings.dashboardUrl} — open settings to change it`, "error");
  }
}

async function hydrateFromActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.url && /^https?:/i.test(tab.url)) {
      $("url").value = tab.url;
    }
  } catch {
    // ignore
  }
}

function wireModeSelector(defaultMode) {
  const segs = document.querySelectorAll(".seg");
  segs.forEach((btn) => {
    if (btn.dataset.mode === defaultMode) btn.classList.add("active");
    else btn.classList.remove("active");
    btn.addEventListener("click", () => {
      segs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      $("resolution").disabled = btn.dataset.mode === "audio";
    });
  });
  $("resolution").disabled = defaultMode === "audio";
}

async function doGrab() {
  const url = $("url").value.trim();
  const projectSlug = $("project").value;
  const mode = getActiveMode();
  const resolution = $("resolution").value;
  const cookiesBrowser = $("cookies-browser").value || undefined;
  const note = $("note").value.trim() || undefined;

  if (!url) return setStatus("paste a URL first", "error");
  if (!projectSlug) return setStatus("pick a project first", "error");

  const settings = await loadSettings();
  const endpoint = `${settings.dashboardUrl.replace(/\/+$/, "")}/api/grab`;

  setStatus("downloading…", "info");
  $("grab").disabled = true;

  try {
    const body = { project_slug: projectSlug, url, mode };
    if (mode !== "audio") body.resolution = resolution;
    if (cookiesBrowser) body.cookies_from_browser = cookiesBrowser;
    if (note) body.note = note;

    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload = null;
    try { payload = await res.json(); } catch { payload = null; }

    if (!res.ok || !payload?.ok) {
      const detail = payload?.error ?? `HTTP ${res.status}`;
      const extra = payload?.stderr ? `\n${payload.stderr.slice(-300)}` : "";
      setStatus(`failed: ${detail}${extra}`, "error");
    } else {
      setStatus(`grabbed into ${projectSlug} ✓`, "ok");
      $("url").value = "";
    }
  } catch (err) {
    setStatus(
      `request failed: ${err instanceof Error ? err.message : err}`,
      "error"
    );
  } finally {
    $("grab").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const settings = await loadSettings();
  wireModeSelector(settings.defaultMode);
  $("resolution").value = settings.defaultResolution;
  $("cookies-browser").value = settings.defaultCookiesBrowser;
  await Promise.all([populateProjects(settings), hydrateFromActiveTab()]);
  $("grab").addEventListener("click", doGrab);
  $("open-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
});
