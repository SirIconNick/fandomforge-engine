// FandomForge web UI — paste-link forensic + human correction.
// Vanilla JS, no build step. All dynamic content uses textContent /
// createElement — no raw HTML injection, no user data interpolated into markup.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

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

async function loadSummary() {
  try {
    const res = await fetch("/api/summary");
    const data = await res.json();
    const parts = [];
    parts.push(`${data.total_forensics} forensics`);
    const t = data.training || {};
    if (t.total) parts.push(`${t.total} training rows`);
    const c = data.corrections || {};
    if (c.total) parts.push(`${c.total} corrections`);
    $("#summary").textContent = parts.join(" · ");
  } catch (e) {
    $("#summary").textContent = "summary unavailable";
  }
}

async function loadBuckets() {
  const grid = $("#bucket-grid");
  try {
    const res = await fetch("/api/buckets");
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
      const card = el("div", {
        class: "bucket-card",
        on: {click: () => showBucketModal(b.name)},
      },
        el("h3", {text: b.name}),
        el("div", {class: "n", text: `n=${b.sample_size} · ${cpm}`}),
        el("div", {style: "margin-top:8px"}, wRows),
      );
      grid.appendChild(card);
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
  const res = await fetch(`/api/bucket/${encodeURIComponent(name)}`);
  if (!res.ok) { alert("bucket not found"); return; }
  const data = await res.json();
  const pre = el("pre", {text: data.markdown || JSON.stringify(data.json, null, 2)});
  const closeBtn = el("button", {
    class: "ghost",
    style: "float:right",
    text: "close",
    on: {click: () => backdrop.remove()},
  });
  const modal = el("div", {class: "modal"},
    closeBtn,
    el("h2", {text: data.name}),
    pre,
  );
  const backdrop = el("div", {
    class: "modal-backdrop",
    on: {click: (ev) => { if (ev.target === backdrop) backdrop.remove(); }},
  }, modal);
  document.body.appendChild(backdrop);
}

async function loadRecent() {
  try {
    const res = await fetch("/api/recent");
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
  } catch (e) { /* non-fatal */ }
}

async function loadCorrections() {
  try {
    const res = await fetch("/api/corrections?limit=50");
    const entries = await res.json();
    const list = $("#corrections-list");
    clear(list);
    if (!entries.length) {
      list.appendChild(el("li", {class: "hint", text: "No corrections yet. Analyze a video and correct it to start training."}));
      return;
    }
    for (const e of entries) {
      list.appendChild(renderCorrectionRow(e));
    }
  } catch (err) {
    const list = $("#corrections-list");
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
    class: "delete",
    text: "delete",
    on: {click: () => deleteCorrection(e.forensic_id)},
  });
  const row = el("li", null, head, delBtn);
  if (e.notes) {
    row.appendChild(el("div", {class: "notes", text: `"${e.notes}"`}));
  }
  return row;
}

async function deleteCorrection(forensicId) {
  if (!confirm(`Delete correction for ${forensicId}?`)) return;
  const res = await fetch(`/api/correct/${encodeURIComponent(forensicId)}`, {method: "DELETE"});
  if (res.ok) {
    loadCorrections();
    loadSummary();
    loadBuckets();
  } else {
    alert("delete failed: " + res.status);
  }
}

async function submitAnalyze(ev) {
  ev.preventDefault();
  const url = $("#url").value.trim();
  const bucket_hint = $("#bucket_hint").value;
  if (!url) return;

  const btn = ev.target.querySelector("button[type=submit]");
  btn.disabled = true;

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url, bucket_hint}),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({detail: "unknown error"}));
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
    const res = await fetch(`/api/job/${currentJobId}`);
    if (!res.ok) { stopPoll(); return; }
    const snap = await res.json();
    renderJobStatus(snap);
    if (snap.status === "done" || snap.status === "failed") {
      stopPoll();
      if (snap.status === "done") renderResult(snap);
      loadRecent();
      loadSummary();
    }
  } catch (e) { /* transient */ }
}

function renderJobStatus(snap) {
  const panel = $("#job-status");
  clear(panel);
  panel.appendChild(el("div", {
    class: "status-line",
    text: `[${snap.status}] elapsed ${snap.elapsed_sec}s · forensic_id: ${snap.forensic_id || "—"}`,
  }));
  const steps = (snap.steps || []).slice(-30);
  for (const s of steps) {
    const lower = s.toLowerCase();
    const cls = (lower.includes("error") || lower.includes("failed")) ? "step-err" : "step";
    panel.appendChild(el("div", {class: cls, text: s}));
  }
  if (snap.error) {
    panel.appendChild(el("div", {class: "step-err", text: "ERROR: " + snap.error}));
  }
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

  // Video preview — point <video> at /api/video/<forensic_id>
  const previewWrap = $("#video-preview-wrap");
  const video = $("#video-preview");
  if (snap.forensic_id) {
    video.src = `/api/video/${encodeURIComponent(snap.forensic_id)}`;
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

  const btn = el("button", {
    class: "primary",
    text: "Correct this analysis",
    on: {click: openCorrection},
  });
  body.appendChild(el("div", {class: "field-row"}, btn));
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

  // Initialize tag editor with current auto-tags
  const autoTags = currentAnalysis.auto_tags || [];
  currentTags = {
    original: autoTags.slice(),
    current: new Set(autoTags),
    removed: new Set(),
    added: new Set(),
  };
  renderTagEditor();

  // Load the bias breakdown for the current bucket so sliders + tooltips
  // reflect real live weights.
  await refreshWeightSliders(currentAnalysis.bucket);

  // When user switches the bucket in the dropdown, refresh the sliders
  $("#corrected_bucket").onchange = (ev) => refreshWeightSliders(ev.target.value);
}

async function refreshWeightSliders(bucket) {
  try {
    const res = await fetch(`/api/effective-weights/${encodeURIComponent(bucket)}`);
    if (!res.ok) {
      renderWeightSliders({}, {});
      return;
    }
    const data = await res.json();
    renderWeightSliders(data.live_effective || {}, data.breakdown || {});
  } catch {
    renderWeightSliders({}, {});
  }
}

function renderTagEditor() {
  const container = $("#tag-editor");
  clear(container);
  // Show current (original - removed + added), with visual state
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
  const chip = el("span", {class: `edit-chip ${state}`.trim()},
    document.createTextNode(text),
    el("button", {
      type: "button",
      text: btnLabel,
      title: btnLabel === "undo" ? "restore tag" : "remove tag",
      on: {click: onClick},
    }),
  );
  return chip;
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

    // Per-layer breakdown tooltip (populated if the server returned one)
    const bd = breakdown[feat] || {};
    const tooltip = el("div", {class: "breakdown"});
    const rows = [
      ["table", bd.table],
      ["forensic", bd.forensic],
      ["training", bd.training === null || bd.training === undefined ? "—" : (bd.training ? "1 (rec ON)" : "0 (rec OFF)")],
      ["correction", bd.correction],
    ];
    for (const [label, val] of rows) {
      const line = document.createTextNode(
        `${label.padEnd(11)}: ${val === null || val === undefined ? "—" : typeof val === "number" ? val.toFixed(2) : val}\n`
      );
      tooltip.appendChild(line);
    }
    tooltip.appendChild(el("span", {class: "hi",
      text: `effective   : ${(bd.effective ?? current).toFixed?.(2) ?? current}`}));

    const row = el("div", {class: "weight-slider-row"},
      el("label", {text: feat}),
      slider,
      display,
      tooltip,
    );
    container.appendChild(row);
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

  // Grab current effective weights as the "original" reference point
  let originalWeights = {};
  try {
    const res = await fetch(`/api/effective-weights/${encodeURIComponent(currentAnalysis.bucket)}`);
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
    const res = await fetch("/api/correct", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const txt = await res.text();
      status.textContent = "failed: " + txt;
      return;
    }
    const data = await res.json();
    status.textContent = data.message || "saved";
    loadSummary();
    loadBuckets();
    loadCorrections();
    // Refresh sliders so the tooltip shows the correction now in place
    refreshWeightSliders(bucket);
  } catch (e) {
    status.textContent = "error: " + e.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#analyze-form").addEventListener("submit", submitAnalyze);
  $("#correct-form").addEventListener("submit", submitCorrection);
  $("#correct-cancel").addEventListener("click", () => {
    $("#correct-panel").classList.add("hidden");
  });
  $("#add-tag").addEventListener("click", addTagFromInput);
  $("#new-tag").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addTagFromInput(); }
  });
  loadSummary();
  loadBuckets();
  loadRecent();
  loadCorrections();
  setInterval(loadSummary, 30000);
});
