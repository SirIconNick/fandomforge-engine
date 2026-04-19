// FandomForge Grab — background service worker
// Registers context menus and handles one-click grabs to the dashboard.

const DEFAULT_SETTINGS = {
  dashboardUrl: "http://localhost:4321",
  defaultProject: "",
  defaultMode: "both",
  defaultResolution: "1080",
  defaultCookiesBrowser: "",
};

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

async function notify(title, message, isError = false) {
  try {
    await chrome.notifications.create({
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      title,
      message,
      priority: isError ? 2 : 0,
    });
  } catch {
    // notifications permission may be disabled; ignore
  }
}

async function postGrab({ url, projectSlug, mode, resolution, cookiesBrowser }) {
  const settings = await loadSettings();
  const dashboard = (settings.dashboardUrl || DEFAULT_SETTINGS.dashboardUrl).replace(/\/+$/, "");
  const endpoint = `${dashboard}/api/grab`;

  const body = {
    project_slug: projectSlug,
    url,
    mode: mode ?? settings.defaultMode,
  };
  if ((mode ?? settings.defaultMode) !== "audio") {
    body.resolution = resolution ?? settings.defaultResolution;
  }
  if (cookiesBrowser || settings.defaultCookiesBrowser) {
    body.cookies_from_browser = cookiesBrowser || settings.defaultCookiesBrowser;
  }

  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }

  if (!res.ok || !payload?.ok) {
    const detail = payload?.error
      ? `${payload.error}${payload.stderr ? ` — ${payload.stderr.slice(-200)}` : ""}`
      : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return payload;
}

async function handleQuickGrab(url, tab) {
  const settings = await loadSettings();
  if (!settings.defaultProject) {
    await notify(
      "FandomForge — no default project",
      "Open the extension options and set a default project, or use the popup.",
      true
    );
    chrome.runtime.openOptionsPage();
    return;
  }
  try {
    await notify("FandomForge", `Grabbing ${url.slice(0, 80)}…`);
    await postGrab({
      url,
      projectSlug: settings.defaultProject,
      mode: settings.defaultMode,
      resolution: settings.defaultResolution,
      cookiesBrowser: settings.defaultCookiesBrowser,
    });
    await notify("FandomForge — done", `Pulled into ${settings.defaultProject}`);
  } catch (err) {
    await notify(
      "FandomForge — grab failed",
      err instanceof Error ? err.message.slice(0, 240) : "Unknown error",
      true
    );
  }
}

function registerMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "ff-grab-link",
      title: "Grab to FandomForge",
      contexts: ["link", "video", "audio"],
    });
    chrome.contextMenus.create({
      id: "ff-grab-page",
      title: "Grab this page URL to FandomForge",
      contexts: ["page"],
    });
  });
}

chrome.runtime.onInstalled.addListener(() => {
  registerMenus();
});
chrome.runtime.onStartup.addListener(() => {
  registerMenus();
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const url =
    info.menuItemId === "ff-grab-page"
      ? tab?.url
      : info.linkUrl || info.srcUrl || tab?.url;
  if (!url) {
    notify("FandomForge", "No URL found in the click context.", true);
    return;
  }
  handleQuickGrab(url, tab);
});

// Popup uses this for server-side grab requests so cookies/permissions are
// centralized in the service worker.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "ff-grab") return;
  postGrab(message.payload)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message }));
  return true;
});
