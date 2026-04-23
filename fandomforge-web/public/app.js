// FandomForge public frontend — deployed on Vercel, talks to a remote
// backend via the user's cloudflared tunnel URL + API key stored in
// localStorage. Plain vanilla JS, no build step, no HTML injection.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const LS_BACKEND_URL = "ff_backend_url";
const LS_API_KEY = "ff_api_key";

const BUCKET_OPTIONS = [
  "multifandom", "action", "narrative", "high_energy", "horror",
  "tribute", "sad", "dance", "hype_trailer", "emotional",
];
const CRAFT_FEATURES = [
  "dropout", "ramp", "triple_cut", "micro_offset", "j_cut",
  "hero_reserve", "diegetic", "lyric_sync", "pose_match", "vlm_apex",
];

let currentJobId = null;
let currentAnalysis = null;
let currentTags = {original: [], current: new Set(), removed: new Set(), added: new Set()};
let pollTimer = null;

// ---------- DOM helpers ----------------------------------------------------

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "on" && typeof v === "object") {
        for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
      } else if (k === "dataset" && typeof v === "object") {
        Object.assign(node.dataset, v);
      } else if (k in node) {
        node[k] = v;
      } else {
        node.setAttribute(k, v);
      }
    }
  }
  for (const c of children) {
    if (c == null) continue;
    if (Array.isArray(c)) { for (const x of c) if (x != null) node.appendChild(x); }
    else if (typeof c === "string") node.appendChild(document.createTextNode(c));
    else node.appendChild(c);
  }
  return node;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

// ---------- settings / backend connection --------------------------------

function getBackendUrl() { return (localStorage.getItem(LS_BACKEND_URL) || "").replace(/\/$/, ""); }
function getApiKey() { return localStorage.getItem(LS_API_KEY) || ""; }

function needsSetup() {
  return !getBackendUrl();
}

function openSetup(prefill = true) {
  $("#setup-panel").classList.remove("hidden");
  $("#analyze-panel").classList.add("hidden");
  $("#setup-panel").scrollIntoView({behavior: "smooth"});
  if (prefill) {
    $("#backend-url").value = getBackendUrl();
    $("#api-key").value = getApiKey();
  }
  $("#setup-status").textContent = "";
}

function closeSetup() {
  $("#setup-panel").classList.add("hidden");
  $("#analyze-panel").classList.remove("hidden");
}

async function saveSetup(ev) {
  ev.preventDefault();
  const backend = $("#backend-url").value.trim().replace(/\/$/, "");
  const key = $("#api-key").value.trim();
  const status = $("#setup-status");
  status.textContent = "testing connection…";

  // Try /api/health (exempt from auth) so we can detect typos cleanly.
  try {
    const res = await fetch(`${backend}/api/health`);
    if (!res.ok) {
      status.textContent = `backend returned HTTP ${res.status} — check the URL`;
      return;
    }
    const h = await res.json();
    if (h.auth_required) {
      // Verify the key actually works before saving
      const res2 = await fetch(`${backend}/api/summary`, {
        headers: {"X-API-Key": key},
      });
      if (res2.status === 401) {
        status.textContent = "API key rejected by backend";
        return;
      }
      if (!res2.ok) {
        status.textContent = `backend returned HTTP ${res2.status}`;
        return;
      }
    }
    localStorage.setItem(LS_BACKEND_URL, backend);
    localStorage.setItem(LS_API_KEY, key);
    status.textContent = "✓ connected";
    setTimeout(() => {
      closeSetup();
      refreshBackendStatus();
      loadAll();
    }, 300);
  } catch (err) {
    status.textContent = `failed: ${err.message} (CORS? wrong URL? laptop asleep?)`;
  }
}

async function refreshBackendStatus() {
  const pill = $("#backend-status");
  const backend = getBackendUrl();
  if (!backend) {
    pill.textContent = "not configured";
    pill.className = "summary-pill warn";
    return;
  }
  try {
    const res = await fetch(`${backend}/api/health`);
    if (!res.ok) {
      pill.textContent = `backend HTTP ${res.status}`;
      pill.className = "summary-pill bad";
      return;
    }
    const h = await res.json();
    // Follow up with summary for the stats line
    try {
      const s = await apiFetch("/api/summary");
      if (s.ok) {
        const d = await s.json();
        const parts = [`${d.total_forensics} forensics`];
        const c = d.corrections || {};
        if (c.total) parts.push(`${c.total} corrections`);
        pill.textContent = parts.join(" · ");
      } else {
        pill.textContent = "connected (summary failed)";
      }
    } catch {
      pill.textContent = "connected";
    }
    pill.className = "summary-pill ok";
  } catch (err) {
    pill.textContent = "offline";
    pill.className = "summary-pill bad";
  }
}

// ---------- authed fetch wrapper -----------------------------------------

async function apiFetch(path, init = {}) {
  const backend = getBackendUrl();
  if (!backend) throw new Error("backend not configured");
  const headers = new Headers(init.headers || {});
  const key = getApiKey();
  if (key) headers.set("X-API-Key", key);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(`${backend}${path}`, {...init, headers});
}

function videoUrl(forensicId) {
  const backend = getBackendUrl();
  const key = getApiKey();
  // The <video> element can't send custom headers, so pass the key as
  // a query param (supported by the auth middleware).
  const params = key ? `?api_key=${encodeURIComponent(key)}` : "";
  return `${backend}/api/video/${encodeURIComponent(forensicId)}${params}`;
}

// ---------- buckets / history / recent -----------------------------------

async function loadBuckets() {
  const grid = $("#bucket-grid");
  try {
    const res = await apiFetch("/api/buckets");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buckets = await res.json();
    clear(grid);
    if (!buckets.length) {
      grid.appendChild(document.createTextNode("No buckets yet — ingest the reference corpus first."));
      return;
    }
    for (const b of buckets) {
      const cpm = `cpm ${fmtRange(b.target_cpm_min, b.target_cpm_max)}`;
      const activeWeights = Object.entries(b.consensus_craft_weights || {})
        .filter(([_, v]) => v >= 0.5)
        .sort((a, c) => c[1] - a[1])
        .slice(0, 4);
      const wRows = activeWeights.length
        ? activeWeights.map(([k, v]) =>
            el("div", {class: "w-row"},
              el("span", {class: "k", text: k}),
              document.createTextNode(`: ${v.toFixed(2)}`),
            )
          )
        : [el("div", {class: "n", text: "no consensus yet"})];
      grid.appendChild(
        el("div", {class: "bucket-card", on: {click: () => showBucketModal(b.name)}},
          el("h3", {text: b.name}),
          el("div", {class: "n", text: `n=${b.sample_size} · ${cpm}`}),
          el("div", {style: "margin-top:8px"}, wRows),
        )
      );
    }
  } catch (e) {
    clear(grid);
    grid.appendChild(document.createTextNode("Failed to load buckets: " + e.message));
  }
}

function fmtRange(min, max) {
  if (min == null || max == null) return "—";
  return `${Math.round(min)}–${Math.round(max)}`;
}

async function showBucketModal(name) {
  const res = await apiFetch(`/api/bucket/${encodeURIComponent(name)}`);
  if (!res.ok) { alert("bucket not found"); return; }
  const data = await res.json();
  const pre = el("pre", {text: data.markdown || JSON.stringify(data.json, null, 2)});
  const closeBtn = el("button", {
    class: "ghost", style: "float:right", text: "close",
    on: {click: () => backdrop.remove()},
  });
  const modal = el("div", {class: "modal"}, closeBtn, el("h2", {text: data.name}), pre);
  const backdrop = el("div", {class: "modal-backdrop",
    on: {click: (ev) => { if (ev.target === backdrop) backdrop.remove(); }}}, modal);
  document.body.appendChild(backdrop);
}

async function loadRecent() {
  try {
    const res = await apiFetch("/api/recent");
    if (!res.ok) return;
    const jobs = await res.json();
    const list = $("#recent-list");
    clear(list);
    if (!jobs.length) {
      list.appendChild(el("li", {class: "hint", text: "No jobs yet this session."}));
      return;
    }
    for (const j of jobs) {
      const started = new Date(j.started_at * 1000).toLocaleTimeString();
      list.appendChild(
        el("li", null,
          el("span", {class: "url", title: j.url, text: j.url}),
          el("span", {class: "n", style: "color:var(--text-dim);font-size:11px", text: started}),
          el("span", {class: `status ${j.status}`, text: j.status}),
        )
      );
    }
  } catch { /* non-fatal */ }
}

async function loadCorrections() {
  const list = $("#corrections-list");
  try {
    const res = await apiFetch("/api/corrections?limit=50");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const entries = await res.json();
    clear(list);
    if (!entries.length) {
      list.appendChild(el("li", {class: "hint", text: "No corrections yet."}));
      return;
    }
    for (const e of entries) list.appendChild(renderCorrectionRow(e));
  } catch (err) {
    clear(list);
    list.appendChild(document.createTextNode("Failed to load corrections: " + err.message));
  }
}

function renderCorrectionRow(e) {
  const ts = e.timestamp ? new Date(e.timestamp).toLocaleString() : "";
  const head = el("div", {class: "row-head"},
    el("span", {class: "bucket-badge", text: e.corrected_bucket || "?"}),
    e.original_bucket && e.original_bucket !== e.corrected_bucket
      ? el("span", {class: "n", style: "font-size:11px;color:var(--text-dim)",
                    text: `(was ${e.original_bucket})`})
      : null,
    el("span", {class: "url-short", title: e.url || "", text: e.url || e.forensic_id || ""}),
    el("span", {class: "timestamp", text: ts}),
  );
  const delBtn = el("button", {
    class: "delete", text: "delete",
    on: {click: () => deleteCorrection(e.forensic_id)},
  });
  const row = el("li", null, head, delBtn);
  if (e.notes) row.appendChild(el("div", {class: "notes", text: `"${e.notes}"`}));
  return row;
}

async function deleteCorrection(forensicId) {
  if (!confirm(`Delete correction for ${forensicId}?`)) return;
  const res = await apiFetch(`/api/correct/${encodeURIComponent(forensicId)}`, {method: "DELETE"});
  if (res.ok) {
    loadCorrections();
    refreshBackendStatus();
    loadBuckets();
  } else {
    alert("delete failed: " + res.status);
  }
}

// ---------- analyze + poll -----------------------------------------------

async function submitAnalyze(ev) {
  ev.preventDefault();
  if (needsSetup()) { openSetup(false); return; }
  const url = $("#url").value.trim();
  const bucket_hint = $("#bucket_hint").value;
  if (!url) return;

  const btn = ev.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const res = await apiFetch("/api/analyze", {
      method: "POST",
      body: JSON.stringify({url, bucket_hint}),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({detail: "unknown"}));
      alert("submit failed: " + (err.detail || res.status));
      return;
    }
    const data = await res.json();
    currentJobId = data.job_id;
    $("#job-status").classList.remove("hidden");
    $("#result-panel").classList.add("hidden");
    $("#correct-panel").classList.add("hidden");
    if (data.cached) {
      renderJobStatus({
        status: "done",
        steps: [`✓ reusing cached forensic (${data.forensic_id})`],
        forensic_id: data.forensic_id,
        elapsed_sec: 0,
      });
      pollJob();
    } else {
      startPoll();
    }
  } finally {
    btn.disabled = false;
    loadRecent();
  }
}

function startPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 1500);
  pollJob();
}
function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function pollJob() {
  if (!currentJobId) return;
  try {
    const res = await apiFetch(`/api/job/${currentJobId}`);
    if (!res.ok) { stopPoll(); return; }
    const snap = await res.json();
    renderJobStatus(snap);
    if (snap.status === "done" || snap.status === "failed") {
      stopPoll();
      if (snap.status === "done") renderResult(snap);
      loadRecent();
      refreshBackendStatus();
    }
  } catch { /* transient */ }
}

function renderJobStatus(snap) {
  const panel = $("#job-status");
  clear(panel);
  panel.appendChild(el("div", {
    class: "status-line",
    text: `[${snap.status}] elapsed ${snap.elapsed_sec}s · forensic_id: ${snap.forensic_id || "—"}`,
  }));
  for (const s of (snap.steps || []).slice(-30)) {
    const lower = s.toLowerCase();
    const cls = (lower.includes("error") || lower.includes("failed")) ? "step-err" : "step";
    panel.appendChild(el("div", {class: cls, text: s}));
  }
  if (snap.error) panel.appendChild(el("div", {class: "step-err", text: "ERROR: " + snap.error}));
  panel.scrollTop = panel.scrollHeight;
}

function renderResult(snap) {
  const a = snap.analysis || {};
  currentAnalysis = a;
  currentAnalysis.forensic_id = snap.forensic_id;
  currentAnalysis.url = snap.url;

  $("#result-panel").classList.remove("hidden");
  const grade = a.projected_grade || "?";
  const gradeEl = $("#result-grade");
  gradeEl.textContent = grade;
  gradeEl.setAttribute("data-grade", grade);

  const previewWrap = $("#video-preview-wrap");
  const video = $("#video-preview");
  if (snap.forensic_id) {
    video.src = videoUrl(snap.forensic_id);
    previewWrap.classList.remove("hidden");
  } else {
    previewWrap.classList.add("hidden");
  }

  const body = $("#result-body");
  clear(body);

  const stats = [
    ["Bucket (auto)", a.bucket || "—"],
    ["Projected score", (a.projected_score || 0).toFixed(1)],
    ["Duration", a.duration_sec ? `${Math.round(a.duration_sec)}s` : "—"],
    ["Shot count", a.shot_count ?? "—"],
  ];
  const grid = el("div", {class: "result-grid"});
  for (const [label, value] of stats) {
    grid.appendChild(
      el("div", {class: "result-stat"},
        el("div", {class: "label", text: label}),
        el("div", {class: "value", text: String(value)}),
      )
    );
  }
  body.appendChild(grid);

  const tags = (a.auto_tags || []);
  if (tags.length) {
    const row = el("div", {class: "tag-row"});
    for (const t of tags) row.appendChild(el("span", {class: "tag-chip", text: t}));
    body.appendChild(row);
  }

  appendFindingList(body, "Strengths", a.strengths);
  appendFindingList(body, "Risks", a.weaknesses);
  appendFindingList(body, "Techniques spotted", a.techniques);

  if (a.summary) body.appendChild(el("p", {class: "hint", text: a.summary}));

  body.appendChild(el("div", {class: "field-row"},
    el("button", {class: "primary", text: "Correct this analysis", on: {click: openCorrection}})));
}

function appendFindingList(parent, title, items) {
  const arr = items || [];
  if (!arr.length) return;
  parent.appendChild(el("h3", {text: title}));
  const ul = el("ul", {class: "finding-list"});
  for (const item of arr) ul.appendChild(el("li", {text: item}));
  parent.appendChild(ul);
}

function populateBucketSelect(selectEl, current) {
  clear(selectEl);
  for (const b of BUCKET_OPTIONS) {
    const opt = el("option", {value: b, text: b});
    if (b === current) opt.selected = true;
    selectEl.appendChild(opt);
  }
}

async function openCorrection() {
  if (!currentAnalysis) return;
  $("#correct-panel").classList.remove("hidden");
  $("#correct-panel").scrollIntoView({behavior: "smooth"});
  populateBucketSelect($("#corrected_bucket"), currentAnalysis.bucket);

  const autoTags = currentAnalysis.auto_tags || [];
  currentTags = {
    original: autoTags.slice(),
    current: new Set(autoTags),
    removed: new Set(),
    added: new Set(),
  };
  renderTagEditor();
  await refreshWeightSliders(currentAnalysis.bucket);
  $("#corrected_bucket").onchange = (ev) => refreshWeightSliders(ev.target.value);
}

async function refreshWeightSliders(bucket) {
  try {
    const res = await apiFetch(`/api/effective-weights/${encodeURIComponent(bucket)}`);
    if (!res.ok) { renderWeightSliders({}, {}); return; }
    const data = await res.json();
    renderWeightSliders(data.live_effective || {}, data.breakdown || {});
  } catch {
    renderWeightSliders({}, {});
  }
}

function renderTagEditor() {
  const container = $("#tag-editor");
  clear(container);
  for (const tag of currentTags.original) {
    if (currentTags.removed.has(tag)) {
      container.appendChild(makeTagChip(tag, "removed", () => {
        currentTags.removed.delete(tag);
        currentTags.current.add(tag);
        renderTagEditor();
      }, "undo"));
    } else {
      container.appendChild(makeTagChip(tag, "", () => {
        currentTags.removed.add(tag);
        currentTags.current.delete(tag);
        renderTagEditor();
      }, "×"));
    }
  }
  for (const tag of currentTags.added) {
    container.appendChild(makeTagChip(tag, "added", () => {
      currentTags.added.delete(tag);
      currentTags.current.delete(tag);
      renderTagEditor();
    }, "×"));
  }
}

function makeTagChip(text, state, onClick, btnLabel) {
  return el("span", {class: `edit-chip ${state}`.trim()},
    document.createTextNode(text),
    el("button", {
      type: "button", text: btnLabel,
      title: btnLabel === "undo" ? "restore tag" : "remove tag",
      on: {click: onClick},
    }),
  );
}

function addTagFromInput() {
  const input = $("#new-tag");
  const val = input.value.trim().toLowerCase().replace(/\s+/g, "-");
  if (!val) return;
  if (currentTags.original.includes(val)) {
    currentTags.removed.delete(val);
    currentTags.current.add(val);
  } else {
    currentTags.added.add(val);
    currentTags.current.add(val);
  }
  input.value = "";
  renderTagEditor();
}

function renderWeightSliders(weights, breakdown) {
  const container = $("#weight-sliders");
  clear(container);
  for (const feat of CRAFT_FEATURES) {
    const current = weights[feat] ?? 0;
    const display = el("span", {class: "wv", text: Number(current).toFixed(2)});
    const slider = el("input", {
      type: "range", min: "0", max: "1", step: "0.05",
      value: String(current),
      dataset: {feat},
      on: {input: () => { display.textContent = Number(slider.value).toFixed(2); }},
    });
    const bd = breakdown[feat] || {};
    const tooltip = el("div", {class: "breakdown"});
    const rows = [
      ["table", bd.table],
      ["forensic", bd.forensic],
      ["training", bd.training === null || bd.training === undefined ? "—"
        : (bd.training ? "1 (rec ON)" : "0 (rec OFF)")],
      ["correction", bd.correction],
    ];
    for (const [label, val] of rows) {
      tooltip.appendChild(document.createTextNode(
        `${label.padEnd(11)}: ${val === null || val === undefined ? "—"
          : typeof val === "number" ? val.toFixed(2) : val}\n`
      ));
    }
    tooltip.appendChild(el("span", {class: "hi",
      text: `effective   : ${(bd.effective ?? current).toFixed?.(2) ?? current}`}));

    container.appendChild(
      el("div", {class: "weight-slider-row"},
        el("label", {text: feat}),
        slider,
        display,
        tooltip,
      )
    );
  }
}

function collectCorrectionWeights() {
  const out = {};
  for (const input of $$("#weight-sliders input[type=range]")) {
    out[input.dataset.feat] = Number(input.value);
  }
  return out;
}

async function submitCorrection(ev) {
  ev.preventDefault();
  if (!currentAnalysis) return;
  const status = $("#correct-status");
  status.textContent = "saving…";
  const bucket = $("#corrected_bucket").value;

  let originalWeights = {};
  try {
    const res = await apiFetch(`/api/effective-weights/${encodeURIComponent(currentAnalysis.bucket)}`);
    if (res.ok) originalWeights = (await res.json()).live_effective || {};
  } catch {}

  const body = {
    forensic_id: currentAnalysis.forensic_id,
    url: currentAnalysis.url || "",
    title: "",
    original_bucket: currentAnalysis.bucket || "",
    corrected_bucket: bucket,
    original_craft_weights: originalWeights,
    corrected_craft_weights: collectCorrectionWeights(),
    tags_added: Array.from(currentTags.added),
    tags_removed: Array.from(currentTags.removed),
    notes: $("#notes").value,
  };
  try {
    const res = await apiFetch("/api/correct", {method: "POST", body: JSON.stringify(body)});
    if (!res.ok) {
      status.textContent = "failed: " + await res.text();
      return;
    }
    const data = await res.json();
    status.textContent = data.message || "saved";
    refreshBackendStatus();
    loadBuckets();
    loadCorrections();
    refreshWeightSliders(bucket);
  } catch (e) {
    status.textContent = "error: " + e.message;
  }
}

// ---------- bootstrap -----------------------------------------------------

function loadAll() {
  loadBuckets();
  loadRecent();
  loadCorrections();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#settings-btn").addEventListener("click", () => openSetup(true));
  $("#setup-form").addEventListener("submit", saveSetup);
  $("#setup-cancel").addEventListener("click", closeSetup);
  $("#analyze-form").addEventListener("submit", submitAnalyze);
  $("#correct-form").addEventListener("submit", submitCorrection);
  $("#correct-cancel").addEventListener("click", () => {
    $("#correct-panel").classList.add("hidden");
  });
  $("#add-tag").addEventListener("click", addTagFromInput);
  $("#new-tag").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addTagFromInput(); }
  });

  if (needsSetup()) {
    openSetup(false);
    refreshBackendStatus();
  } else {
    refreshBackendStatus();
    loadAll();
    setInterval(refreshBackendStatus, 30000);
  }
});
