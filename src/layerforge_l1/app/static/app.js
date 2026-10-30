// LayerForge L4 client. All geometry, validation, CAD and printability come from the
// server (the approved L0-L3 core); this file only renders reports and builds edit
// requests in the documented L2 edit format.

const ui = {
  info: null,
  projects: [],
  name: null,          // current project
  tab: "proposal",
  state: null,         // GET /api/projects/<name>
  job: null,           // active job being polled
  error: null,         // {title, message, hint, report}
  sheet: null,         // selected sheet id
  view: "front",       // editor image: front (assembled, default) | sheet | changes_base | changes_previous
  tool: "inspect",
  precision: "vector",
  barWidth: 1.2,
  drag: null,          // {from:[x,y], to:[x,y]} while drawing
  pending: null,       // edit op awaiting "Apply"
  marker: null,        // [x,y] mm highlighted on the canvas
  hover: null,
  process: null,       // printer/process form values
  upload: null,        // File chosen for import
  kitChecks: {},       // kit id -> checksum verification result
  sections: {},        // collapsible section key -> open, kept across re-renders
  rimAll: true,        // rim setting applies to every layer unless unchecked
  bambu: false,        // also write a Bambu Studio multi-plate project with the kit
  bambuPrinter: null,  // Bambu printer preset for that project
  confirmDelete: null, // kit id whose inline delete confirmation is shown
  kitNotice: null,     // result of the last kit delete / open-folder action
  kitBuildOpen: false, // kit creation form shown via "+ New Print Kit" (always shown before the first kit)
  detailChoices: {},   // intake detail id -> {action, color} chosen but not yet re-run
  upscaleFactor: 2,    // Upscale & Retry: processing-resolution factor shown
  upscaleApproved: {}, // preview id -> the user approved its visible changes
  resizePrinter: null, // Bambu printer whose usable area limits the sheet size ("" = the process bed; null = as last checked)
  resizeSize: null,    // sheet size (mm) entered for the resize preview
  resizeApproved: {},  // resize preview id -> {size, changes} approvals
  confirmProject: null, // project name whose inline delete confirmation is shown
  projectNotice: null,  // result of the last project deletion (shown on the project list)
};

const TABS = [
  ["proposal", "Import & proposal"],
  ["review", "Review & edit"],
  ["printability", "Printability"],
  ["kit", "Print kit"],
];

const TOOLS = [
  ["inspect", "Inspect", "Hover to read coordinates; click to mark a point."],
  ["fill_hole", "Fill cutout", "Click inside a cutout of the selected sheet to close it."],
  ["remove_island", "Remove island", "Click a floating island of the selected sheet to delete it."],
  ["split", "Split region", "Click a visible region of the selected sheet to move it to a new sheet."],
  ["add_circle", "Add circle", "Drag from the centre outwards to add material."],
  ["subtract_circle", "Cut circle", "Drag from the centre outwards to cut a round hole."],
  ["add_rect", "Add rectangle", "Drag a rectangle to add material."],
  ["subtract_rect", "Cut rectangle", "Drag a rectangle to cut a hole."],
  ["subtract_bar", "Cut slot", "Drag a line; cuts a slot of the given width (e.g. to widen a narrow slit)."],
  ["bridge", "Bridge", "Drag a line; adds a bar of the given width (e.g. to connect an island)."],
];

const PROCESS_FIELDS = [
  ["nozzle_diameter_mm", "Nozzle Ø (mm)"],
  ["line_width_mm", "Line width (mm)"],
  ["layer_height_mm", "Layer height (mm)"],
  ["first_layer_height_mm", "First layer (mm)"],
  ["min_opening_mm", "Min opening (mm)"],
  ["min_positive_feature_mm", "Min material width (mm)"],
  ["bed_x_mm", "Bed X (mm)"],
  ["bed_y_mm", "Bed Y (mm)"],
  ["max_z_mm", "Max Z (mm)"],
  ["filament_density_g_cm3", "Density (g/cm³)"],
];

const CHANGE_KINDS = {
  visible_artwork: ["bad", "Visible change", "The front view changed. If intended, use “Accept front view as artwork”; otherwise undo."],
  visible_subgrid_geometry: ["warn", "Visible sub-pixel change", "Exact geometry changed where it can be seen, below the grid resolution."],
  artwork_reference: ["neutral", "Artwork reference updated", "The reviewed artwork now matches the current front view."],
  hidden_geometry: ["ok", "Hidden change only", "Material changed behind other sheets; the front view is unchanged."],
  physical_parameters: ["ok", "Physical parameters", "Color, thickness or rim changed; geometry of the front view is unchanged."],
  no_change: ["neutral", "No change", "The edit did not change anything."],
};

// ---------------------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const r3 = (v) => Math.round(v * 1000) / 1000;
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(d)) : esc(v));
const badge = (cls, text, title = "") => `<span class="badge ${cls}"${title ? ` title="${esc(title)}"` : ""}>${esc(text)}</span>`;
const swatch = (hex, big = false) => `<span class="swatch${big ? " big" : ""}" style="background:${esc(hex)}" title="${esc(hex)}"></span>`;
const yesno = (v) => (v === null || v === undefined ? "–" : v === true || v === "PASS" ? '<span class="yes">✓</span>' : '<span class="no">✗</span>');
// `open` attribute for a collapsible section: the user's last choice, else the default.
const openAttr = (key, byDefault = false, force = false) => (force || (ui.sections[key] ?? byDefault) ? "open" : "");
const busy = () => Boolean(ui.job && (ui.job.status === "queued" || ui.job.status === "running"));

async function api(method, path, body, raw) {
  const options = { method, headers: {} };
  if (raw) {
    options.body = raw;
    options.headers["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    options.body = JSON.stringify(body);
    options.headers["Content-Type"] = "application/json";
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    throw { message: "The LayerForge server is not reachable. Is `python -m layerforge_l1 app` still running?", hint: String(err) };
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw { message: data.error?.message || `HTTP ${response.status}`, hint: data.error?.hint, status: response.status };
  return data;
}

function showError(err, title = "Request failed") {
  ui.error = { title, message: err.message || String(err), hint: err.hint, report: err.report };
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---------------------------------------------------------------------------- data flow
async function loadProjects() {
  ui.projects = (await api("GET", "/api/projects")).projects;
}

async function loadState() {
  if (!ui.name) return;
  ui.state = await api("GET", `/api/projects/${encodeURIComponent(ui.name)}`);
  const sheets = ui.state.draft?.sheets || [];
  if (!sheets.some((s) => s.id === ui.sheet)) ui.sheet = sheets.length ? sheets[sheets.length - 1].id : null;
  const job = ui.state.job;
  if (job && (job.status === "queued" || job.status === "running")) {
    if (!busy()) pollJob(job);
  } else if (!busy()) {
    ui.job = job;
  }
}

async function submit(path, body, label) {
  if (busy()) return;
  ui.error = null;
  try {
    const data = await api("POST", path, body);
    ui.pending = null;
    pollJob(data.job, label);
  } catch (err) {
    showError(err);
  }
}

let pollTimer = null;
function pollJob(job, label) {
  ui.job = { ...job, label };
  render();
  clearTimeout(pollTimer);
  const tick = async () => {
    try {
      const fresh = await api("GET", `/api/jobs/${job.id}`);
      ui.job = { ...fresh, label: ui.job?.label };
      if (fresh.status === "done" || fresh.status === "failed") {
        if (fresh.status === "failed") {
          ui.error = { title: `${jobLabel(fresh)} failed`, ...fresh.error };
        }
        await loadProjects();
        await loadState();
        afterJob(fresh);
        render();
        return;
      }
      renderJob();
    } catch (err) {
      ui.job = null;
      showError(err, "Lost contact with a running job");
      return;
    }
    pollTimer = setTimeout(tick, 700);
  };
  pollTimer = setTimeout(tick, 400);
}

function afterJob(job) {
  if (job.status !== "done") return;
  if (job.kind === "propose" || job.kind === "upscale_preview") ui.tab = "proposal";
  if (job.kind === "upscale_retry") { ui.tab = "proposal"; ui.upscaleApproved = {}; }
  if (job.kind === "resize_analysis") { setTab("proposal"); ui.resizeSize = null; }
  if (job.kind === "resize_preview" || job.kind === "resize_search") setTab("proposal");
  if (job.kind === "resize_retry") { setTab("proposal"); ui.resizeApproved = {}; ui.resizeSize = null; }
  if (job.kind === "start_review") setTab("review");
  if (job.kind === "kit") { setTab("kit"); ui.kitBuildOpen = false; }
  if (job.kind === "printability") setTab("printability");
}

function jobLabel(job) {
  return {
    propose: "L1 proposal (planning, validation, CAD check)",
    upscale_preview: "Upscale preview (intake at the finer resolution, comparison)",
    resize_analysis: "Checking printability at larger sheet sizes (FDM estimate)",
    resize_preview: "Previewing the artwork at the new sheet size",
    resize_search: "Trial planning at larger sheet sizes (no CAD)",
    resize_retry: "Resize & retry (L1 proposal at the new size, then the FDM estimate)",
    upscale_retry: "Upscale & Retry (L1 proposal at the finer resolution)",
    start_review: "Starting the review",
    edit: "Applying the edit",
    undo: "Undo / revert (replaying the edit log)",
    verify: "Verifying save/reload (replaying the edit log)",
    printability: "FDM printability estimate",
    kit: "Building the print kit (export, reopen, compare, estimate)",
  }[job.kind] || job.kind;
}

// ---------------------------------------------------------------------------- routing
function setTab(tab) {
  ui.tab = tab;
  if (ui.name) history.replaceState(null, "", `#/${encodeURIComponent(ui.name)}/${tab}`);
}

async function route() {
  const [, name, tab] = location.hash.replace(/^#/, "").split("/").map(decodeURIComponent);
  ui.error = null;
  if (name && name !== ui.name) {
    ui.name = name;
    ui.state = null;
    ui.pending = null;
    ui.marker = null;
    ui.sheet = null;
    ui.job = null;
    try {
      await loadState();
    } catch (err) {
      ui.name = null;
      showError(err, "Cannot open project");
      return;
    }
  } else if (!name) {
    ui.name = null;
    ui.state = null;
  }
  ui.tab = TABS.some(([t]) => t === tab) ? tab : defaultTab();
  render();
}

function defaultTab() {
  const stage = ui.state?.stage;
  return stage === "kit" ? "kit" : stage === "review" ? "review" : "proposal";
}

// ---------------------------------------------------------------------------- rendering
function render() {
  renderProjectSelect();
  const root = $("#app");
  if (!ui.name) {
    root.innerHTML = errorBanner() + importView();
    bindImport();
    return;
  }
  if (!ui.state) {
    root.innerHTML = errorBanner() + '<p class="muted pad">Loading project…</p>';
    return;
  }
  root.innerHTML = `
    <div class="spread" style="margin-bottom:12px">
      <div><h1>${esc(ui.state.name)}</h1>
        <p class="muted small">Source <code>${esc(ui.state.record.source.original_name)}</code> · sha256 ${esc(ui.state.record.source.sha256.slice(0, 12))}… · scale ${esc(ui.state.record.proposal_settings.scale)} · ${esc(ui.state.record.proposal_settings.outer_mm)} mm sheet</p>
      </div>
      ${ui.confirmProject === ui.state.name ? "" : `<button class="small ghost danger" data-action="delete-project-ask" data-project="${esc(ui.state.name)}" data-testid="project-delete" ${busy() ? 'disabled title="Wait for the running job to finish"' : ""}>Delete project…</button>`}
    </div>
    ${ui.confirmProject === ui.state.name ? projectDeleteConfirm(ui.state.name) : ""}
    ${statusStrip()}
    <div id="job-slot">${jobBanner()}</div>
    ${errorBanner()}
    <nav class="tabs" role="tablist">${TABS.map(([id, label], i) => `
      <button class="tab ${ui.tab === id ? "current" : ""}" role="tab" aria-selected="${ui.tab === id}" data-action="tab" data-tab="${id}" ${tabEnabled(id) ? "" : "disabled"}>
        <span class="step">${i + 1}</span>${esc(label)}</button>`).join("")}
    </nav>
    <section id="tab-body">${tabBody()}</section>`;
  if (ui.tab === "review" && ui.state.draft) {
    bindCanvas();
    drawOverlay();
  }
}

function tabEnabled(id) {
  return id === "proposal" || Boolean(ui.state.draft);
}

function tabBody() {
  if (ui.tab === "review" && ui.state.draft) return reviewView();
  if (ui.tab === "printability" && ui.state.draft) return printabilityView();
  if (ui.tab === "kit" && ui.state.draft) return kitView();
  return proposalView();
}

function renderProjectSelect() {
  const select = $("#project-select");
  const options = ['<option value="">Open project…</option>'].concat(
    ui.projects.map((p) => `<option value="${esc(p.name)}" ${p.name === ui.name ? "selected" : ""}>${esc(p.name)} · ${esc(p.stage)}</option>`));
  select.innerHTML = options.join("");
  $("#footer").innerHTML = ui.info ? `LayerForge ${esc(ui.info.version)} · workspace <code>${esc(ui.info.workspace)}</code> · local only` : "";
}

function errorBanner() {
  if (!ui.error) return "";
  const e = ui.error;
  return `<div class="banner error" role="alert">
    <div class="spread"><strong>${esc(e.title || "Error")}</strong><button class="small ghost" data-action="dismiss-error">Dismiss</button></div>
    <p>${esc(e.message)}</p>
    ${e.hint ? `<p class="small muted">${esc(e.hint)}</p>` : ""}
    ${e.report ? `<details><summary>Details</summary><pre class="mono small">${esc(JSON.stringify(e.report, null, 2))}</pre></details>` : ""}
  </div>`;
}

function jobBanner() {
  const job = ui.job;
  if (!job || !(job.status === "queued" || job.status === "running")) return "";
  const last = job.last_event;
  const events = (job.events || []).slice(-8).reverse().map((e) => {
    const extra = Object.entries(e).filter(([k]) => !["stage", "elapsed_s"].includes(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
    return `<div class="mono small muted">${esc(e.elapsed_s)} s · ${esc(e.stage)} ${esc(extra.slice(0, 160))}</div>`;
  }).join("");
  return `<div class="banner info job" role="status" data-testid="job-banner">
    <div class="spinner" aria-hidden="true"></div>
    <div style="flex:1">
      <div class="spread"><strong>${esc(jobLabel(job))}</strong><span class="muted coords">${job.status === "queued" ? "queued" : `${fmt(job.elapsed_s, 1)} s`}</span></div>
      <div class="small">${last ? `Current step: <code>${esc(last.stage)}</code>` : "Waiting for the worker…"} — the page stays usable; results appear when the job finishes.</div>
      <div class="events">${events}</div>
    </div>
  </div>`;
}

function renderJob() {
  const slot = $("#job-slot");
  if (slot) slot.innerHTML = jobBanner();
}

// ----------------------------------------------------------------------- status strip
function latestKit() {
  return (ui.state.kits || [])[0] || null;
}

function statusStrip() {
  const s = ui.state;
  const draft = s.draft;
  const kit = latestKit();
  let geo;
  if (!draft) {
    geo = ["neutral", "No reviewed stack yet", "Pick a candidate from the proposal to start the review."];
  } else if (draft.status.printable) {
    const cad = kit && !kit.stale
      ? (kit.status.cad === "CAD_VALID" ? `CAD verified by reopening every STEP/STL (${kit.id}).` : `CAD INVALID in ${kit.id}.`)
      : kit ? "Latest kit is out of date: the draft changed after it was built." : "CAD not exported yet (build a print kit).";
    geo = [kit && !kit.stale && kit.status.cad !== "CAD_VALID" ? "bad" : "ok", "Valid geometry", `Validators PASS. ${cad}`];
  } else {
    geo = ["bad", "Invalid draft", `${draft.validation.failures.length} validation failure(s); not exportable until fixed.`];
  }
  let fdm = ["neutral", "Not estimated", "Run the printability estimate or build a kit."];
  const est = kit && !kit.stale ? { status: kit.printability.status, counts: kit.printability.counts, from: `kit ${kit.id}` }
    : s.printability && !s.printability.stale ? { status: s.printability.status, counts: s.printability.counts, from: "draft estimate" } : null;
  if (est) {
    const cls = est.status === "NO_RISKS_FOUND" ? "ok" : est.status === "RISKS_REPORTED" ? "warn" : "bad";
    fdm = [cls, fdmLabel(est.status), `${est.counts.UNSUPPORTED} unsupported · ${est.counts.RISK} risk (${est.from}). Geometric estimate, not a slicer result.`];
  } else if (s.printability || kit) {
    fdm = ["neutral", "Estimate out of date", "The draft changed since the last estimate."];
  }
  return `<div class="status-strip" data-testid="status-strip">
    ${statusCard("Geometry / CAD", geo, "status-geometry")}
    ${statusCard("FDM printability (estimate)", fdm, "status-fdm")}
    <div class="status-card neutral physical" data-testid="status-physical">
      <div class="label">Physical print</div>
      <div class="value">${badge("outline", "NOT VERIFIED")}</div>
      <div class="note">${esc(s.physical.note)} Test print L-G10 is pending.</div>
    </div>
  </div>`;
}

function statusCard(label, [cls, value, note], testid) {
  return `<div class="status-card ${cls}" data-testid="${testid}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="note">${esc(note)}</div></div>`;
}

function fdmLabel(status) {
  return { NO_RISKS_FOUND: "No risks found (estimate)", RISKS_REPORTED: "Risks reported (estimate)", UNSUPPORTED_FEATURES: "Unsupported features (estimate)" }[status] || status;
}

// ------------------------------------------------------------------------------ import
function importView() {
  const scales = ui.info?.scales || [1, 2, 4];
  return `<div class="grid2">
    <div class="panel stack">
      <h1>New project</h1>
      <p class="muted">Import a flat-color illustration (PNG or JPEG). LayerForge proposes physical layer sheets with the approved L1 planner, then you review, edit and export a print kit.</p>
      <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Choose an image">
        ${ui.upload ? `<strong>${esc(ui.upload.name)}</strong> <span class="muted">(${(ui.upload.size / 1024).toFixed(0)} KB)</span><img id="upload-preview" alt="selected image">` : "<strong>Drop an image here</strong><br><span class=\"muted\">or click to choose a file</span>"}
        <input type="file" id="file-input" accept=".png,.jpg,.jpeg,.jfif,.jpe,image/png,image/jpeg" hidden>
      </div>
      <div class="form-grid">
        <label class="field"><span>Project name</span><input id="f-name" placeholder="from file name"></label>
        <label class="field"><span>Grid scale</span><select id="f-scale">${scales.map((s) => `<option value="${s}" ${s === 2 ? "selected" : ""}>${s} · ${(0.390625 / s).toFixed(3)} mm/px${s === 1 ? " · fast" : s === 4 ? " · slow" : ""}</option>`).join("")}</select></label>
        <label class="field"><span>Sheet size (mm)</span><input id="f-outer" type="number" value="225" min="50" step="1"></label>
        <label class="field"><span>Sheet thickness (mm)</span><input id="f-thick" type="number" value="1.6" min="0.2" step="0.2"></label>
        <label class="field"><span>Max colors</span><input id="f-colors" type="number" value="12" min="2" max="32"></label>
      </div>
      <p class="small muted">The artwork occupies the sheet minus a 12.5 mm shared perimeter. Scale 2 is the default; scale 4 can take many minutes and much memory.</p>
      <div class="row"><button class="primary" data-action="create" ${ui.upload && !busy() ? "" : "disabled"}>Import and propose layers</button></div>
    </div>
    <div class="panel stack">
      <h2>Projects</h2>
      ${projectNotice()}
      ${ui.projects.length ? `<table><thead><tr><th>Name</th><th>Stage</th><th>Created (UTC)</th><th></th></tr></thead><tbody>${ui.projects.map((p) => `
        <tr data-testid="project-row" data-project="${esc(p.name)}"><td><a href="#/${encodeURIComponent(p.name)}">${esc(p.name)}</a></td><td>${esc(p.stage)}</td><td class="small muted">${esc(p.created_utc)}</td>
          <td>${ui.confirmProject === p.name ? "" : `<button class="small ghost danger" data-action="delete-project-ask" data-project="${esc(p.name)}" data-testid="project-delete">Delete…</button>`}</td></tr>
        ${ui.confirmProject === p.name ? `<tr><td colspan="4">${projectDeleteConfirm(p.name)}</td></tr>` : ""}`).join("")}</tbody></table>`
        : '<p class="muted">No projects yet.</p>'}
      <p class="small muted">Projects are saved in the workspace folder after every step and reopen after a restart.</p>
    </div>
  </div>`;
}

function bindImport() {
  const zone = $("#dropzone");
  const input = $("#file-input");
  if (!zone) return;
  const choose = (file) => {
    if (!file) return;
    ui.upload = file;
    render();
  };
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") input.click(); });
  input.addEventListener("change", () => choose(input.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); choose(e.dataTransfer.files[0]); });
  const preview = $("#upload-preview");
  if (preview && ui.upload) preview.src = URL.createObjectURL(ui.upload);
}

async function createProject() {
  if (!ui.upload || busy()) return;
  const params = new URLSearchParams({
    filename: ui.upload.name,
    scale: $("#f-scale").value,
    outer_mm: $("#f-outer").value,
    thickness_mm: $("#f-thick").value,
    max_colors: $("#f-colors").value,
  });
  if ($("#f-name").value.trim()) params.set("name", $("#f-name").value.trim());
  ui.error = null;
  try {
    const data = await api("POST", `/api/projects?${params}`, undefined, ui.upload);
    ui.upload = null;
    await loadProjects();
    location.hash = `#/${encodeURIComponent(data.project)}/proposal`;
    pollJob(data.job);
  } catch (err) {
    showError(err, "Import failed");
  }
}

// ---------------------------------------------------------------------------- proposal
function proposalView() {
  const p = ui.state.proposal;
  const record = ui.state.record;
  if (!p) {
    return `<div class="panel stack">
      <h2>Proposal</h2>
      <p class="muted">${busy() ? "The L1 proposal is running: color intake, the approved planner for each candidate, validation, and a STEP/STL export of the selected candidate that is reopened and checked." : "No proposal yet."}</p>
      ${busy() ? "" : `<button class="primary" data-action="propose">Run the proposal</button>`}
      <figure style="max-width:280px"><img class="img" src="${esc(ui.state.source_url)}" alt="source image"><figcaption>Source image as uploaded</figcaption></figure>
    </div>`;
  }
  const ready = p.status === "PROPOSAL_READY_FOR_REVIEW";
  const reviewing = record.candidate;
  const flags = (p.flags || []).map((f) => `<li>${badge(f.severity === "BLOCKER" ? "bad" : f.severity === "WARN" ? "warn" : "neutral", f.severity)} <strong>${esc(f.code)}</strong> — ${esc(f.message)}</li>`).join("");
  const rows = p.candidates.map((c) => `
    <tr class="${c.name === p.selected ? "selected" : ""}">
      <td><strong>${esc(c.name)}</strong>${c.name === p.selected ? " " + badge("ok", "selected") : ""}${c.name === reviewing ? " " + badge("neutral", "in review") : ""}</td>
      <td>${badge(c.status === "VALID" ? "ok" : "bad", c.status)}</td>
      <td>${fmt(c.sheet_count)}</td>
      <td>${(c.front_to_back_colors || []).map((h) => swatch(h)).join(" ")}</td>
      <td>${fmt(c.source_regions_split_across_sheets)}${c.seam_length_mm != null ? ` <span class="muted small">(${fmt(c.seam_length_mm)} mm)</span>` : ""}${c.cell_refinement ? " " + badge("neutral", "refined cells") : ""}</td>
      <td>${fmt(c.reconstruction_mismatched_pixels)}</td>
      <td>${fmt(c.planning_seconds, 1)}</td>
      <td>${c.status === "PLANNING_FAILED" ? `<span class="small muted">${esc(c.reason || "")}</span>` :
        reviewing ? "" : `<button class="small ${c.name === p.selected ? "primary" : ""}" data-action="start-review" data-candidate="${esc(c.name)}" ${busy() ? "disabled" : ""}>Review this candidate</button>`}</td>
    </tr>`).join("");
  const img = p.images || {};
  const obstruction = p.candidates.filter((c) => c.obstruction_url);
  return `<div class="panel stack">
      <div class="spread">
        <h2>L1 proposal ${badge(ready ? "ok" : "warn", ready ? "Ready for review" : "Needs your decision")}</h2>
        <span class="muted small">${fmt(p.seconds, 1)} s · automatic proposal (no manual layer plan)</span>
      </div>
      ${ready ? "" : `<div class="banner warn">No candidate passed every check. Nothing was edited automatically. ${obstruction.length ? "The obstruction map shows where the planner cannot support the artwork; widen or remove those features in the image and import it again." : "See the flags below."}${ui.state.upscale?.available ? " You can also try <strong>Upscale &amp; Retry</strong> below." : ""}</div>`}
      ${ui.state.upscale && ui.state.upscale.scale !== ui.state.upscale.import_scale ? `<p class="small muted" data-testid="processing-scale">Processing resolution: scale ${ui.state.upscale.scale} (imported at scale ${ui.state.upscale.import_scale}; raised by Upscale &amp; Retry). Physical size unchanged.</p>` : ""}
      <div class="row"><h4 style="margin:0">Palette</h4>${(p.palette || []).map((h) => swatch(h, true)).join("")}${p.frame_color ? `<span class="small muted">frame ${esc(p.frame_color)}</span>` : ""}</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Candidate</th><th>Status</th><th>Sheets</th><th>Colors front → back</th><th>Split regions (seams)</th><th>Mismatch px</th><th>Time s</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${reviewing ? `<p class="small">Reviewing <strong>${esc(reviewing)}</strong>. <a href="#/${encodeURIComponent(ui.name)}/review" data-action="tab" data-tab="review">Open the editor →</a></p>` : ""}
      ${flags ? `<div><h4>Flags to review</h4><ul class="small">${flags}</ul></div>` : ""}
      ${p.cad ? `<p class="small muted">Selected candidate CAD check: reopened STL ${p.cad.revalidation_pass ? "PASS" : "FAIL"}, stack mismatch ${fmt(p.cad.stack_mismatch_px)} px, STEP single solids ${p.cad.all_step_valid_single_solid ? "yes" : "no"}, watertight STL ${p.cad.all_stl_watertight_single_component ? "yes" : "no"}.</p>` : ""}
    </div>
    ${resizePanel()}
    ${upscalePanel()}
    ${detailsPanel(p, reviewing)}
    ${attemptsPanel()}
    <div class="panel">
      <h3>Proposal previews <span class="muted small">(selected candidate, drawn from the reopened STL)</span></h3>
      <div class="gallery">
        <figure><img class="img" src="${esc(ui.state.source_url)}" alt="source"><figcaption>Source image</figcaption></figure>
        ${img.target ? `<figure><img class="img" src="${esc(img.target)}" alt="target"><figcaption>Flat-color target after intake</figcaption></figure>` : ""}
        ${img.assembled_front ? `<figure><img class="img" src="${esc(img.assembled_front)}" alt="assembled"><figcaption>Assembled front view</figcaption></figure>` : ""}
        ${img.exploded ? `<figure><img class="img" src="${esc(img.exploded)}" alt="exploded"><figcaption>Exploded stack</figcaption></figure>` : ""}
        ${obstruction.map((c) => `<figure><img class="img" src="${esc(c.obstruction_url)}" alt="obstruction"><figcaption>${esc(c.name)}: cells the planner cannot support</figcaption></figure>`).join("")}
      </div>
      ${img.source_comparison ? `<figure style="margin-top:12px"><img class="img" src="${esc(img.source_comparison)}" alt="comparison"><figcaption>Target | assembled | mismatches in red</figcaption></figure>` : ""}
    </div>`;
}

// Physical size: measure the findings now, tabulate them against larger sheets that fit the
// printer, preview one size (same pixels, every length times the ratio), retry after approval.
const SEV = { UNSUPPORTED: "bad", RISK: "warn", CLEARED: "ok" };

function dims(d) {
  return `${fmt(d.sheet_mm[0], 1)} × ${fmt(d.sheet_mm[1], 1)} mm sheet · artwork ${d.artwork_mm.map((v) => fmt(v, 1)).join(" × ")} mm · perimeter band ${fmt(d.perimeter_band_mm, 2)} mm · ${fmt(d.sheet_thickness_mm, 2)} mm thick`;
}

function resizePanel() {
  const r = ui.state.resize;
  if (!r) return "";
  const a = r.analysis;
  const env = ui.info?.bambu;
  const last = r.last_check;
  const chosen = ui.resizePrinter ?? a?.bambu_printer ?? "";
  const printers = env ? `<label class="field"><span>Usable area of</span><select id="resize-printer" ${busy() ? "disabled" : ""}>
      <option value="">the process bed (${fmt(Number(ui.process.bed_x_mm), 0)} × ${fmt(Number(ui.process.bed_y_mm), 0)} mm)</option>
      ${env.printers.map((x) => `<option value="${esc(x.preset)}" ${x.preset === chosen ? "selected" : ""}>${esc(x.model)} (keep-out zones excluded)</option>`).join("")}</select></label>` : "";
  return `<section class="panel stack" data-testid="resize-panel">
    <h3>Physical size ${r.sheet_mm !== r.import_sheet_mm ? badge("neutral", `resized from ${fmt(r.import_sheet_mm, 1)} mm`) : ""}</h3>
    <p class="small">Every printability threshold is in millimetres (planner structural width, nozzle, minimum opening and material width). A larger sheet widens every opening, gap and material strip by the same ratio, so some findings can be cleared by size alone; others (topology, unmeasured necks) cannot. Nothing is resized without your approval; the image is never edited.</p>
    ${last ? resizeCheck(last) : ""}
    ${r.available ? `<details class="section" data-section="resize-process" ${openAttr("resize-process")}><summary>Printer and process for the check: <strong>${esc(ui.process.name)}</strong></summary>${processForm("rs")}</details>
    <div class="row">${printers}<button class="${a ? "" : "primary"}" data-action="resize-analyze" data-testid="resize-analyze" ${busy() ? "disabled" : ""}>Check printability at larger sizes</button></div>`
      : `<p class="small muted">${esc(r.reason || "")}</p>`}
    ${a ? resizeAnalysis(a) : ""}
    ${a && r.preview ? resizePreview(r.preview, a) : ""}
  </section>`;
}

// A finding's location, with a Locate button once a review of the same sheets exists.
function locateCell(sheet, at) {
  const where = at ? `${at.map((v) => fmt(v, 1)).join(", ")} mm` : "–";
  const inDraft = ui.state.draft?.sheets?.some((x) => x.id === sheet);
  return `${esc(where)}${inDraft && at ? ` <button class="small" data-action="locate" data-sheet="${esc(sheet)}" data-x="${at[0]}" data-y="${at[1]}" data-testid="resize-locate">Locate</button>` : ""}`;
}

function resizeCheck(c) {
  const after = c.after;
  const row = (label, kind, v, sheets) => `<tr><td>${label}</td><td>${kind}</td><td>${v ? `${v.UNSUPPORTED} unsupported · ${v.RISK} risk` : "–"}</td><td>${sheets ?? "–"}</td></tr>`;
  const left = after?.unsupported || [];
  const reviewing = ui.state.record.candidate;
  const sameCandidate = reviewing && after && reviewing === after.candidate;
  const rows = left.map((u, i) => `<tr data-testid="resize-unsupported"><td>${i + 1}</td><td>${esc(u.kind.replace(/_/g, " "))}</td><td>${fmt(u.width_mm, 2)} mm</td><td><code>${esc(u.sheet)}</code></td><td>${locateCell(u.sheet, u.at_mm)}</td></tr>`).join("");
  const path = !left.length ? "" : `<div class="banner info" data-testid="resize-manual-path"><strong>Correct these manually in the Review editor</strong>
      <ol class="small" style="margin:6px 0 0">
        <li>${sameCandidate ? `Review of <code>${esc(reviewing)}</code> is open: use <strong>Locate</strong> above (or in Printability).` : reviewing ? `A review of another candidate exists; Locate works for sheets it shares.` : `Start the review of <code>${esc(after.candidate)}</code>: <button class="small primary" data-action="start-review" data-candidate="${esc(after.candidate)}" ${busy() ? "disabled" : ""}>Review this candidate</button> — then <strong>Locate</strong> appears here and in Printability.`}</li>
        <li>On that sheet, widen the opening with <strong>Cut slot</strong> or <strong>Cut rectangle</strong>, or close it with <strong>Fill cutout</strong>. Every edit is re-validated; a change that alters the front view must be accepted with <strong>Accept front view as artwork</strong> (your approval).</li>
        <li>Run the printability estimate again on the edited draft.</li></ol></div>`;
  return `<div class="subpanel stack" data-testid="resize-check"><h4 style="margin:0">Last resize: ${fmt(c.from_mm, 1)} → ${fmt(c.to_mm, 1)} mm ${badge(c.proposal_status === "PROPOSAL_READY_FOR_REVIEW" ? "ok" : "warn", c.proposal_status)}</h4>
    <div class="table-wrap"><table><thead><tr><th></th><th>Kind</th><th>Findings (${esc(c.process.name)})</th><th>Sheets</th></tr></thead><tbody>
      ${row(`Before, at ${fmt(c.from_mm, 1)} mm`, badge("neutral", "measured"), c.before.counts, c.before.sheet_count)}
      ${row("Predicted for the new size", badge("outline", "estimate, not a result"), c.predicted)}
      ${row(`After re-planning at ${fmt(c.to_mm, 1)} mm${after ? ` (${esc(after.candidate)})` : ""}`, badge(after ? "ok" : "neutral", "measured result"), after?.counts, after?.sheet_count)}</tbody></table></div>
    ${c.more_unsupported_than_predicted ? `<div class="banner warn" data-testid="resize-prediction-missed">The measured result has <strong>${c.more_unsupported_than_predicted}</strong> more unsupported finding(s) than predicted. Re-planning at the new size changes the sheets (and intake can keep details the smaller size merged), so the prediction is only a guide; the measured row is what counts.</div>` : ""}
    ${left.length ? `<h4 style="margin:0">Still unsupported after re-planning ${badge("bad", String(left.length))}</h4>
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Finding</th><th>Width</th><th>Sheet</th><th>Location (x, y)</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${after.map_urls?.length ? `<details data-section="resize-maps" ${openAttr("resize-maps")}><summary>Maps (unsupported red, risk orange)</summary><div class="gallery">${after.map_urls.map((u) => `<figure><img class="img" src="${esc(u)}" alt="map" loading="lazy"><figcaption>${esc(u.split("/").pop().split("?")[0])}</figcaption></figure>`).join("")}</div></details>` : ""}
      ${path}` : after ? `<p class="small">No unsupported findings were measured after re-planning${after.counts.RISK ? `; ${after.counts.RISK} risk finding(s) remain` : ""}.</p>` : ""}
    <p class="small muted">Measured findings come from the FDM estimate of the re-planned stack (an estimate, not a slicer); ${badge("outline", "NOT VERIFIED")} until printed. Geometric validation does not guarantee a successful print. Proposal time ${fmt(c.proposal_seconds, 0)} s.</p></div>`;
}

function resizeAnalysis(a) {
  const o = a.options;
  const rec = o.recommended;
  const marked = (row) => (rec && row.sheet_mm === rec.sheet_mm ? badge("ok", "smallest useful") : o.clears_most_risks && row.sheet_mm === o.clears_most_risks.sheet_mm ? badge("neutral", "fewest risks") : row.sheet_mm === o.current_mm ? badge("neutral", "current") : "");
  const rows = o.table.map((row) => `<tr class="${rec && row.sheet_mm === rec.sheet_mm ? "selected" : ""}" data-testid="resize-row"><td>${fmt(row.sheet_mm, 0)} mm ${marked(row)}</td><td>×${fmt(row.factor, 3)}</td><td>${row.UNSUPPORTED}</td><td>${row.RISK}</td><td>${row.CLEARED}</td></tr>`).join("");
  const size = ui.resizeSize ?? rec?.sheet_mm ?? Math.min(o.max_mm, o.current_mm + 1);
  return `<div class="stack" data-testid="resize-analysis">
    ${a.current ? "" : '<div class="banner warn">The plan or settings changed since this check; check again.</div>'}
    <p class="small">Now: ${a.measured.counts.UNSUPPORTED} unsupported · ${a.measured.counts.RISK} risk (${esc(a.measured.source)}). Largest sheet that fits: <strong>${fmt(o.max_mm, 0)} mm</strong> (${esc(a.limit.basis.join("; "))}).</p>
    <div class="table-wrap"><table><thead><tr><th>Sheet size</th><th>Ratio</th><th>Unsupported (predicted)</th><th>Risk (predicted)</th><th>Cleared (predicted)</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small">${badge("outline", "prediction")} ${rec ? `Smallest useful larger size: <strong>${fmt(rec.sheet_mm, 0)} mm</strong> (predicted ${rec.UNSUPPORTED} unsupported and ${rec.RISK} risk — an estimate, not a guarantee).` : "No larger sheet that fits this printer is predicted to improve the findings."}
      ${o.unsupported_not_fixable_within_printer.length ? ` ${o.unsupported_not_fixable_within_printer.length} unsupported finding(s) would need a sheet larger than the printer allows.` : ""}
      ${o.not_size_related.length ? ` ${o.not_size_related.length} finding(s) are not size-related and stay.` : ""}
      Predictions multiply each measured width by the ratio; the retry re-plans and re-measures, and only that measured result counts.</p>
    ${a.trial_search?.available ? trialSearch(a.trial_search, a.current) : ""}
    ${a.current && o.max_mm > o.current_mm ? `<div class="row"><label class="field"><span>New sheet size (mm)</span><input type="number" id="resize-size" min="${o.current_mm + 1}" max="${o.max_mm}" step="1" value="${esc(size)}"></label>
      <button data-action="resize-preview" data-testid="resize-preview" ${busy() ? "disabled" : ""}>Preview this size</button></div>` : ""}
  </div>`;
}

function trialSearch(t, current) {
  const rows = (t.trials || []).map((r) => `<tr data-testid="resize-trial"><td>${fmt(r.sheet_mm, 0)} mm</td><td>${r.plans ? badge("ok", "plans") : badge("bad", "fails")}</td>
      <td>${fmt(r.sheet_count)}</td><td class="small">${esc(r.reason || (r.refined ? "after local cell refinement" : ""))}</td><td>${fmt(r.seconds, 0)} s</td></tr>`).join("");
  return `<div class="subpanel stack" data-testid="resize-trial-search">
    <p class="small">Planning failed: ${esc((t.planning_failures || []).join("; "))}. ${esc(t.why)}.</p>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>Sheet</th><th>Trial</th><th>Sheets</th><th>Note</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small">${t.smallest_plannable_mm ? `Smallest size that planned (to ${fmt(t.step_mm, 0)} mm): <strong>${fmt(t.smallest_plannable_mm, 0)} mm</strong>.` : "No size that fits this printer planned."} Trials use one beam score and no CAD; the retry runs the full proposal.</p>`
      : `<div class="row"><button data-action="resize-search" data-testid="resize-search" ${current && !busy() ? "" : "disabled"}>Find the smallest size that plans</button>
        <span class="small muted">Plans the largest fitting size first, then bisects; roughly ${fmt(t.estimated_minutes_per_trial, 1)} min per trial.</span></div>`}
  </div>`;
}

function resizePreview(v, a) {
  const ok = v.status === "READY_TO_RESIZE" && v.current;
  const ap = ui.resizeApproved[v.preview_id] || {};
  const changes = v.changes || [];
  const canRetry = ok && ap.size && (ap.changes || !changes.length) && !busy();
  const checks = v.boundaries.checks.map((c) => `<tr><td>${yesno(c.pass)}</td><td>${esc(c.check)}</td></tr>`).join("");
  const list = (rows, max = 12) => rows.slice(0, max).map((i) => `<li>${badge(SEV[i.severity] || "neutral", i.severity)} → predicted ${badge(SEV[i.predicted] || "neutral", i.predicted)} ${esc(i.kind.replace(/_/g, " "))}${i.width_mm != null ? ` ${fmt(i.width_mm, 2)} → ~${fmt(i.predicted_width_mm, 2)} mm` : ""} at ${locateCell(i.sheet, i.at_mm)}${i.note ? ` <span class="muted">(${esc(i.note)})</span>` : ""}</li>`).join("") + (rows.length > max ? `<li class="muted">… ${rows.length - max} more</li>` : "");
  const pr = v.prediction;
  const changeRows = changes.map((c) => `<tr data-testid="resize-change"><td>${c.number}</td><td style="width:360px"><img class="img" style="max-width:360px" src="${esc(c.image_url)}" alt="current | source | new" loading="lazy"></td>
      <td class="small">${swatch(c.previous_hex)} ${esc(c.previous_hex)} → ${swatch(c.new_hex)} ${esc(c.new_hex)} · ${fmt(c.area_mm2, 2)} mm²</td></tr>`).join("");
  const review = a.basis.stage === "review";
  return `<div class="stack" data-testid="resize-preview-result">
    <h4 style="margin:0">Preview: ${fmt(v.from_mm, 1)} → ${fmt(v.to_mm, 1)} mm (×${fmt(v.ratio, 3)}) ${badge(ok ? "ok" : "bad", v.current ? v.status : "out of date")}</h4>
    <div class="table-wrap"><table><tbody><tr><td>Current</td><td class="small">${dims(v.dimensions_now)}</td></tr><tr><td>Proposed</td><td class="small">${dims(v.dimensions_new)}</td></tr></tbody></table></div>
    <details class="section" data-section="resize-boundaries" ${openAttr("resize-boundaries", !v.boundaries.pass)}><summary><strong>Dimensions and boundaries</strong> ${badge(v.boundaries.pass ? "ok" : "bad", v.boundaries.pass ? "all checks pass" : "failed")}</summary>
      <div class="table-wrap"><table><tbody>${checks}</tbody></table></div></details>
    <div><h4>Printability — prediction ${badge("outline", "estimate, not a result")}</h4>
      <p class="small">Predicted: ${pr.cleared.length} cleared · ${pr.improved.length} improved (unsupported → risk) · ${pr.remaining.length} remain. ${esc(v.prediction_basis)}. The actual findings are measured after the retry.</p>
      ${pr.improved.length || pr.cleared.length ? `<details data-section="resize-improve" ${openAttr("resize-improve")}><summary>Improve (${pr.cleared.length + pr.improved.length})</summary><ul class="small">${list([...pr.improved, ...pr.cleared])}</ul></details>` : ""}
      ${pr.remaining.length ? `<details data-section="resize-remain" ${openAttr("resize-remain", true)}><summary>Remain (${pr.remaining.length})</summary><ul class="small">${list(pr.remaining)}</ul></details>` : ""}</div>
    <div><h4>Artwork fidelity ${badge(changes.length ? "warn" : "ok", `${changes.length} visible change${changes.length === 1 ? "" : "s"}`)}</h4>
      <p class="small muted">Same pixels and colors; the intake filters work in millimetres, so a larger sheet can keep details a smaller one merged. ${(v.details || []).length} intake detail(s) at this size (removed pieces such as eye highlights are kept by default).</p>
      ${v.images?.comparison ? `<figure><img class="img" style="max-width:720px" src="${esc(v.images.comparison)}" alt="current target | new target"><figcaption>Current target | target at the new size (changes outlined)</figcaption></figure>` : ""}
      ${changeRows ? `<div class="table-wrap"><table><tbody>${changeRows}</tbody></table></div>` : ""}</div>
    ${ok ? `${review ? '<div class="banner warn">A review exists: it is archived with this attempt and a new proposal is planned at the new size; review edits are not carried over.</div>' : ""}
      <label class="check"><input type="checkbox" data-resize-approve="size" data-preview="${esc(v.preview_id)}" data-testid="resize-approve-size" ${ap.size ? "checked" : ""}> I approve changing the sheet from ${fmt(v.from_mm, 1)} mm to ${fmt(v.to_mm, 1)} mm</label>
      ${changes.length ? `<label class="check"><input type="checkbox" data-resize-approve="changes" data-preview="${esc(v.preview_id)}" data-testid="resize-approve-changes" ${ap.changes ? "checked" : ""}> I approve the ${changes.length} visible artwork change(s) above</label>` : ""}
      <div class="row"><button class="primary" data-action="resize-retry" data-preview="${esc(v.preview_id)}" data-testid="resize-retry" ${canRetry ? "" : "disabled"}>Resize to ${fmt(v.to_mm, 1)} mm &amp; retry</button>
        <span class="small muted">The current attempt (proposal, review, estimate) is archived in this project; the image is unchanged.</span></div>` : ""}
  </div>`;
}

// Upscale & Retry: only while the proposal has failed. Preview (intake at the finer grid,
// boundary checks, visible changes) first; the retry needs approval of every listed change.
function upscalePanel() {
  const u = ui.state.upscale;
  if (!u?.available) return "";
  const option = u.options.find((o) => o.factor === ui.upscaleFactor) || u.options[0];
  const preview = u.previews?.[String(option.factor)];
  const radios = u.options.map((o) => `<label class="check" title="${esc(o.reason || "")}">
      <input type="radio" name="upscale-factor" value="${o.factor}" ${o.factor === option.factor ? "checked" : ""} ${busy() ? "disabled" : ""}>
      <strong>${o.factor}×</strong> <span class="small muted">scale ${o.scale} · ${fmt(o.mm_per_pixel, 4)} mm/px · est. peak ${fmt(o.estimated_peak_gb, 1)} GB, ${fmt(o.available_gb, 1)} GB free now${o.estimated_minutes != null ? ` · roughly ${fmt(o.estimated_minutes, 0)} min or more` : ""}</span>
      ${o.fits ? "" : badge("bad", "not enough memory", o.reason)}</label>`).join("");
  return `<section class="panel stack" data-testid="upscale-panel">
    <h3>Upscale &amp; Retry <span class="small muted">(processing resolution, not physical size)</span></h3>
    <p class="small">Re-run intake and planning in this project with 2× or 4× more pixels per millimetre. Only the processing resolution changes: the sheet size, artwork aperture, shared perimeter, artwork position and aspect stay exactly as they are, and every physical rule and minimum width is unchanged. The image is resampled by the normal intake (no AI upscaling, no invented detail).
      <strong>A finer grid can represent thin details more faithfully, but it does not necessarily resolve physical or topological constraints</strong> (openings narrower than the structural width, unsupportable islands).</p>
    <div class="stack" style="gap:4px">${radios}</div>
    <div class="row"><button class="${preview ? "" : "primary"}" data-action="upscale-preview" data-testid="upscale-preview" ${option.fits && !busy() ? "" : "disabled"}>Preview at ${option.factor}×</button>
      <span class="small muted">Intake only (no planning); the current proposal stays until you retry.</span></div>
    ${preview ? upscalePreview(preview) : ""}
  </section>`;
}

function upscalePreview(v) {
  const ok = v.status === "READY_TO_RETRY" && v.current;
  const changes = v.changes || [];
  const approved = Boolean(ui.upscaleApproved[v.preview_id]);
  const canRetry = ok && (approved || !changes.length) && !busy();
  const detail = (c) => Object.entries(c).filter(([k]) => !["check", "pass"].includes(k))
    .map(([k, x]) => `${k.replace(/_/g, " ")}: ${Array.isArray(x) ? x.join(" × ") : x}`).join(" · ");
  const checks = (v.boundaries?.checks || []).map((c) => `<tr><td>${yesno(c.pass)}</td><td>${esc(c.check)}</td><td class="small muted">${esc(detail(c))}</td></tr>`).join("");
  const rows = changes.map((c) => `<tr data-testid="upscale-change"><td>${c.number}</td>
      <td style="width:360px;min-width:200px"><img class="img" style="max-width:360px" src="${esc(c.image_url)}" alt="previous | resampled source | new" loading="lazy"></td>
      <td class="small">${swatch(c.previous_hex)} ${esc(c.previous_hex)} → ${swatch(c.new_hex)} ${esc(c.new_hex)}<br>${fmt(c.area_mm2, 2)} mm² at x ${fmt(c.center_mm[0], 1)}, y ${fmt(c.center_mm[1], 1)} mm</td></tr>`).join("");
  const prev = (v.previous_details || []).map((d) => `<li>${d.kept ? badge("ok", "kept") : badge("bad", "changed")} ${esc(d.id)} ${swatch(d.planned_hex)} ${esc(d.planned_hex)} (${Math.round(d.kept_fraction * 100)} % still there)</li>`).join("");
  const carry = v.decision_carry || {};
  const notes = (v.physical_notes || []).map((f) => `<li><strong>${esc(f.code)}</strong> — ${esc(f.message)}</li>`).join("");
  const added = (v.palette || []).filter((h) => !(v.previous_palette || []).includes(h));
  const removed = (v.previous_palette || []).filter((h) => !(v.palette || []).includes(h));
  return `<div class="stack" data-testid="upscale-preview-result">
    <h4 style="margin:0">Preview at ${v.factor}× (scale ${v.from_scale} → ${v.to_scale}) ${badge(ok ? "ok" : "bad", v.current ? v.status : "out of date")}</h4>
    ${v.current ? "" : '<div class="banner warn">The image, settings or detail choices changed since this preview; preview again.</div>'}
    ${v.status === "BOUNDARY_CHECK_FAILED" ? '<div class="banner error">The physical boundaries could not be preserved at this resolution. Nothing will be retried.</div>' : ""}
    ${v.status === "INSUFFICIENT_MEMORY" ? `<div class="banner error">Not enough memory: ${esc(v.resources?.reason || "")}</div>` : ""}
    ${v.status === "NO_DEMONSTRATED_BENEFIT" ? '<div class="banner info">At this resolution the processed artwork does not change visibly, so a finer grid is not shown to help; no retry is offered. Consider the physical size instead.</div>' : ""}
    ${v.source ? `<p class="small">Source ${esc(v.source.size_px.join(" × "))} px → ${fmt(v.artwork_mm_per_pixel, 4)} mm per processing pixel (was ${fmt(v.previous_scale_mm_per_pixel, 4)}): ${esc(v.source.resampling)}.</p>` : ""}
    ${checks ? `<details class="section" data-section="upscale-boundaries" ${openAttr("upscale-boundaries", !v.boundaries.pass)}><summary><strong>Physical dimensions and boundaries</strong> ${badge(v.boundaries.pass ? "ok" : "bad", v.boundaries.pass ? "all preserved" : "failed")}</summary>
      <div class="table-wrap"><table><tbody>${checks}</tbody></table></div></details>` : ""}
    ${v.images?.comparison ? `<figure><img class="img" style="max-width:720px" src="${esc(v.images.comparison)}" alt="previous target | new target with changes outlined"><figcaption>Previous target | new target (visible changes outlined in magenta, numbered)</figcaption></figure>` : ""}
    <div><h4>Visible artwork changes ${badge(changes.length ? "warn" : "ok", String(changes.length))}</h4>
      <p class="small muted">Coherent regions whose color differs from the previous target (previous | resampled source | new). Outline shifts of up to one previous pixel are expected at a finer grid and are not listed (${fmt((v.change_summary?.changed_fraction || 0) * 100, 2)} % of the artwork pixels differ in total).${added.length || removed.length ? ` Palette: ${added.map((h) => `+${swatch(h)} ${esc(h)}`).join(" ")} ${removed.map((h) => `−${swatch(h)} ${esc(h)}`).join(" ")}` : " Palette unchanged."}</p>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>Previous | source | new</th><th>Change</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}</div>
    ${prev ? `<div><h4>Details of the previous proposal</h4><ul class="small">${prev}</ul></div>` : ""}
    <p class="small">Intake details at this resolution: ${(v.details || []).length} (eye whites and other removed pieces are kept by default)${carry.carried?.length ? `; ${carry.carried.length} of your earlier choices carried over` : ""}${carry.not_found_at_new_resolution?.length ? `; ${carry.not_found_at_new_resolution.length} earlier choices have no matching detail any more (${esc(carry.not_found_at_new_resolution.join(", "))})` : ""}${carry.conflicts?.length ? `; ${carry.conflicts.length} conflicting choices fall back to the default` : ""}. After the retry they are listed under “Details changed by intake”, where you can change them.</p>
    ${notes ? `<div><h4>Physical printability notes <span class="small muted">(separate from visual fidelity; upscaling does not change them)</span></h4><ul class="small">${notes}</ul></div>` : ""}
    ${ok ? `${changes.length ? `<label class="check"><input type="checkbox" data-upscale-approve="${esc(v.preview_id)}" data-testid="upscale-approve" ${approved ? "checked" : ""} ${busy() ? "disabled" : ""}> I approve these ${changes.length} visible changes to the artwork</label>` : ""}
      <div class="row"><button class="primary" data-action="upscale-retry" data-factor="${v.factor}" data-preview="${esc(v.preview_id)}" data-testid="upscale-retry" ${canRetry ? "" : "disabled"}>Retry at ${v.factor}× (scale ${v.to_scale})</button>
        <span class="small muted">The failed proposal and its diagnostics are archived in this project.</span></div>` : ""}
  </div>`;
}

function attemptsPanel() {
  const attempts = ui.state.upscale?.attempts || [];
  if (!attempts.length) return "";
  const rows = attempts.map((a) => `<tr data-testid="attempt">
      <td><strong>${esc(a.id)}</strong><br><span class="small muted">${esc(a.archived_utc)}</span></td>
      <td>scale ${a.scale}<br>${badge(a.status === "PROPOSAL_READY_FOR_REVIEW" ? "neutral" : "bad", a.status)} ${(a.blockers || []).map((b) => badge("neutral", b)).join(" ")}</td>
      <td class="small">${a.candidates.map((c) => `${esc(c.name)}: ${esc(c.status)}${c.reason ? ` — ${esc(c.reason)}` : ""}`).join("<br>")}</td>
      <td class="small">${a.sheet_mm ? `${fmt(a.sheet_mm, 1)} mm sheet · ` : ""}replaced by ${a.replaced_by.kind === "physical_resize" ? `resize to ${fmt(a.replaced_by.sheet_mm, 1)} mm` : `${a.replaced_by.upscale_factor}× processing (scale ${a.replaced_by.scale})`}: ${esc(a.replaced_by.status)}; ${a.replaced_by.approved_visible_changes} visible changes approved</td>
      <td class="small"><a href="${esc(a.images.target)}" target="_blank" rel="noopener">target</a>${a.images.obstruction.map((u) => ` · <a href="${esc(u)}" target="_blank" rel="noopener">obstruction map</a>`).join("")} · ${a.images.comparison ? `<a href="${esc(a.images.comparison)}" target="_blank" rel="noopener">preview comparison</a>` : ""}</td></tr>`).join("");
  return `<details class="panel section" data-section="attempts" data-testid="attempts" ${openAttr("attempts")}>
    <summary><h3>Previous attempts ${badge("neutral", String(attempts.length))}</h3><span class="small muted">archived proposals with their diagnostics and settings</span></summary>
    <div class="section-body"><div class="table-wrap"><table><thead><tr><th>Attempt</th><th>Result</th><th>Candidates</th><th>Retry</th><th>Evidence</th></tr></thead><tbody>${rows}</tbody></table></div></div>
  </details>`;
}

// Details intake would recolor or erase: original | default intake | as planned, with a choice each.
function detailChoice(p, d) {
  return ui.detailChoices[d.id] || p.detail_decisions?.[d.id] || { action: d.action.action, color: d.action.color };
}

function detailsPanel(p, reviewing) {
  const details = p.details || [];
  if (!details.length) return "";
  const kindLabel = { color_not_in_palette: "color not in the palette", removed_by_cleanup: "removed by speckle cleanup" };
  const changed = details.some((d) => {
    const c = detailChoice(p, d);
    return c.action !== d.action.action || (c.action === "adjust" && (c.color || "").toUpperCase() !== (d.action.color || "").toUpperCase());
  });
  const rows = details.map((d, i) => {
    const c = detailChoice(p, d);
    const name = `detail-${d.id}`;
    const opt = (value, label) => `<label class="check small"><input type="radio" name="${esc(name)}" value="${value}" data-detail="${esc(d.id)}" ${c.action === value ? "checked" : ""} ${reviewing ? "disabled" : ""}> ${label}</label>`;
    return `<tr data-testid="intake-detail" data-detail="${esc(d.id)}">
      <td>${i + 1}</td>
      <td style="width:360px;min-width:240px"><img class="img" style="max-width:360px" src="${esc(d.image_url)}" alt="original | default intake | as planned" loading="lazy"></td>
      <td class="small"><strong>${esc(kindLabel[d.kind] || d.kind)}</strong><br>
        ${swatch(d.original_hex)} ${esc(d.original_hex)} → ${swatch(d.processed_hex)} ${esc(d.processed_hex)} by default<br>
        ${fmt(d.area_mm2, 2)} mm² · ${fmt(d.width_mm, 2)} mm wide${d.size_notes.length ? `<br><span class="muted">${esc(d.size_notes.join("; "))} (size only; the printability estimate is separate)</span>` : ""}<br>
        Planned: ${swatch(d.result_hex)} ${esc(d.result_hex)} ${d.action.default ? badge("neutral", "default") : badge("ok", "your choice")}</td>
      <td class="small"><div class="stack" style="gap:2px">
        ${opt("preserve", d.kind === "color_not_in_palette" ? "Preserve (own color, adds a filament)" : "Preserve")}
        ${opt("remove", d.kind === "color_not_in_palette" ? "Remove (take the surrounding color)" : "Remove")}
        ${opt("adjust", "Adjust to color")}
        <input type="color" value="${esc((c.color || d.processed_hex).toLowerCase())}" data-detail-color="${esc(d.id)}" ${c.action === "adjust" && !reviewing ? "" : "disabled"}>
      </div></td>
    </tr>`;
  }).join("");
  return `<details class="panel section" data-section="intake-details" data-testid="intake-details" ${openAttr("intake-details", true)}>
    <summary><h3>Details changed by intake ${badge("warn", `${details.length}`)}</h3>
      <span class="small muted">original | default intake | as planned · nothing is discarded without your choice</span></summary>
    <div class="section-body stack">
      <p class="small muted">Removed pieces (e.g. eye whites) are kept by default, however small. Colors missing from the palette (e.g. lips, gold) keep the nearest palette color until you choose: preserving one adds its color as a new filament.${p.palette_added?.length ? ` Added by your choices: ${p.palette_added.map((h) => `${swatch(h)} ${esc(h)}`).join(" ")}.` : ""}</p>
      ${p.images?.details_overview ? `<details data-section="intake-details-overview" ${openAttr("intake-details-overview")}><summary>Where they are (numbered)</summary><img class="img" style="max-width:640px" src="${esc(p.images.details_overview)}" alt="details overview"></details>` : ""}
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Original | default | planned</th><th>Detail</th><th>Choice</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${reviewing ? '<p class="small muted">A review has started; the proposal (and these choices) can no longer be re-run for this project.</p>'
        : `<div class="row"><button class="${changed ? "primary" : ""}" data-action="apply-details" data-testid="apply-details" ${busy() ? "disabled" : ""}>Re-run the proposal with these choices</button>
          ${changed ? '<span class="small">Choices changed: re-run to plan them.</span>' : ""}</div>`}
    </div>
  </details>`;
}

// ------------------------------------------------------------------------------ review
function selectedSheet() {
  return ui.state.draft.sheets.find((s) => s.id === ui.sheet) || null;
}

function reviewView() {
  const d = ui.state.draft;
  const sheet = selectedSheet();
  return `${reviewBar()}
  <div class="editor">
    <aside class="editor-left">${layersPanel()}${historyPanel()}</aside>
    <div class="editor-main">
      ${draftBanner()}
      <div class="panel tight stage">${canvasPanel(sheet)}</div>
      ${validationPanel()}
      ${changePanel()}
      ${previewsPanel()}
      ${advancedPanel()}
    </div>
    <aside class="editor-right">${sheet ? sheetPanel(sheet) : '<div class="panel tight muted small">Select a layer to edit it.</div>'}</aside>
  </div>`;
}

// One bar with the draft's state and every next step: edits are saved automatically, so
// "save" here means proving the saved project reloads identically.
function reviewBar() {
  const d = ui.state.draft;
  const ok = d.status.printable;
  const dis = busy() ? "disabled" : "";
  const v = ui.state.verify;
  const verified = v ? (v.replay_identical
    ? `<span class="small"><span class="yes">✓</span> Reloading and replaying all ${v.edits} edits reproduces the saved geometry exactly.</span>`
    : `<span class="small"><span class="no">✗</span> Replay differs: ${esc(v.differences.join(", "))}</span>`) : "";
  return `<div class="review-bar" data-testid="review-bar">
    <div class="review-bar-status">
      ${badge(ok ? "ok" : "bad", ok ? "Valid draft" : "Invalid draft")}
      <span class="small muted">${d.sheets.length} layers · ${fmt(d.total_height_mm)} mm stack · ${d.edits.length} edit${d.edits.length === 1 ? "" : "s"} · saved automatically</span>
    </div>
    <div class="review-bar-actions">
      <button class="small" data-action="undo" ${d.edits.length ? dis : "disabled"} title="Undo the last edit">↶ Undo last edit</button>
      <button class="small" data-action="verify" ${dis} title="Replay the saved edit log from the base and compare">Verify save/reload</button>
      <button class="small" data-action="propose-op" data-op='${esc(JSON.stringify({ op: "accept_artwork" }))}' ${dis}
        title="Only for an intended visible change: the current front view becomes the reviewed artwork">Accept front view as artwork…</button>
      <span class="divider" aria-hidden="true"></span>
      <button class="small" data-action="tab" data-tab="printability">Printability →</button>
      <button class="small primary" data-action="tab" data-tab="kit" ${ok ? "" : "disabled"} title="${ok ? "Export STEP/STL for every layer" : "Invalid drafts cannot be exported"}">Build print kit →</button>
    </div>
    ${verified ? `<div class="review-bar-note">${verified} <span class="muted small">(${esc(v.checked_utc)})</span></div>` : ""}
  </div>`;
}

function layersPanel() {
  const d = ui.state.draft;
  const frontToBack = [...d.sheets].reverse();
  const list = frontToBack.map((s, i) => {
    const fails = s.checks.pass === false;
    return `
    <li class="layer ${s.id === ui.sheet ? "selected" : ""} ${fails ? "failing" : ""}" data-action="select-sheet" data-sheet="${esc(s.id)}" tabindex="0" data-testid="layer-row" title="${esc(s.export_name)} · ${esc(s.color)}">
      <span class="layer-index">${i + 1}</span>
      ${swatch(s.color, true)}
      <div class="layer-text"><div class="name">${esc(s.export_name)}</div>
        <div class="meta">${fmt(s.thickness_mm)} mm${s.rim ? ` · rim ${fmt(s.rim.width_mm)}×${fmt(s.rim.total_height_mm)} mm` : ""} · ${s.cutout_holes} cutout${s.cutout_holes === 1 ? "" : "s"}${s.islands ? ` · <span class="no">${s.islands} island${s.islands === 1 ? "" : "s"}</span>` : ""}${s.geometry === "exact vector" ? " · vector" : ""}</div></div>
      ${fails ? badge("bad", "fails") : ""}
    </li>`;
  }).join("");
  return `<div class="panel tight layers-panel">
    <div class="spread"><h3 style="margin:0">Layers</h3><span class="small muted">${d.sheets.length} · front first</span></div>
    <div class="end-label">Front (viewer side)</div>
    <ul class="layer-list">${list}</ul>
    <div class="end-label">Back</div>
    <p class="small muted" style="margin:6px 0 0">One filament color per layer · total ${fmt(d.total_height_mm)} mm</p>
  </div>`;
}

function draftBanner() {
  const d = ui.state.draft;
  if (d.stale) return `<div class="banner warn">The saved project changed outside this view. <button class="small" data-action="reload">Reload</button></div>`;
  if (d.previews.notice) return `<div class="banner warn"><strong>Draft invalid.</strong> ${esc(d.previews.notice)}</div>`;
  return "";
}

const TOOL_GROUPS = [
  ["", ["inspect"]],
  ["Add", ["add_circle", "add_rect", "bridge"]],
  ["Cut", ["subtract_circle", "subtract_rect", "subtract_bar"]],
  ["Fix", ["fill_hole", "remove_island", "split"]],
];

function canvasPanel(sheet) {
  const d = ui.state.draft;
  const images = d.previews.images;
  const views = [["front", "Assembled"], ["sheet", "Selected layer"], ["changes_base", "Changes vs base"], ["changes_previous", "Changes vs previous"]]
    .filter(([v]) => v === "sheet" || (v === "front" && images.assembled_front) || (v === "changes_base" && images.changes_vs_base) || (v === "changes_previous" && images.changes_vs_previous));
  const tool = TOOLS.find(([t]) => t === ui.tool);
  const needsWidth = ui.tool === "bridge" || ui.tool === "subtract_bar";
  const shaped = ["add_circle", "subtract_circle", "add_rect", "subtract_rect", "subtract_bar", "bridge"].includes(ui.tool);
  const label = (t) => TOOLS.find(([x]) => x === t);
  const caption = {
    front: "Assembled front view of all layers",
    sheet: sheet ? `Layer ${sheet.export_name} alone` : "No layer selected",
    changes_base: "Changes since the base proposal",
    changes_previous: "Changes made by the last action",
  }[ui.view] || "";
  return `
    <div class="stage-head">
      <div class="segmented" role="tablist" aria-label="View">${views.map(([v, l]) => `<button class="small ${ui.view === v ? "active" : ""}" data-action="view" data-view="${v}" role="tab" aria-selected="${ui.view === v}">${l}</button>`).join("")}</div>
      <span class="small muted">${esc(caption)}</span>
    </div>
    <div class="canvas-wrap" id="canvas-wrap">
      <img id="edit-image" src="${esc(viewImage(sheet))}" alt="${esc(caption)}" draggable="false">
      <canvas id="overlay" aria-label="Edit canvas"></canvas>
    </div>
    <div class="spread small stage-foot">
      <span class="coords muted" id="coords">${ui.hover ? `x ${ui.hover[0].toFixed(2)} mm · y ${ui.hover[1].toFixed(2)} mm` : "Coordinates in mm from the bottom-left sheet corner"}</span>
      ${ui.view.startsWith("changes") ? '<span class="legend"><span><i style="background:#ff0000"></i>visible change</span><span><i style="background:#285aff"></i>hidden change</span></span>' : ui.view === "sheet" ? '<span class="legend"><span><i style="background:#ebebeb;border:1px solid #bbb"></i>cutout</span></span>' : ""}
    </div>
    <div class="tools">
      <div class="tools-head small"><strong>Edit ${sheet ? `${swatch(sheet.color)} ${esc(sheet.export_name)}` : "—"}</strong>
        <span class="muted">${esc(tool ? tool[2] : "")}</span></div>
      <div class="toolbar" role="toolbar" aria-label="Edit tools">
        ${TOOL_GROUPS.map(([group, tools]) => `<div class="tool-group">${group ? `<span class="tool-group-label">${group}</span>` : ""}${tools.map((t) => {
          const [, l, h] = label(t);
          return `<button class="small ${ui.tool === t ? "active" : ""}" data-action="tool" data-tool="${t}" title="${esc(h)}" ${busy() ? "disabled" : ""}>${l}</button>`;
        }).join("")}</div>`).join("")}
      </div>
      ${needsWidth || shaped ? `<div class="row small tool-options">
        ${needsWidth ? `<label class="check">width <input type="number" id="bar-width" value="${ui.barWidth}" min="0.1" step="0.1" style="width:70px"> mm</label>` : ""}
        ${shaped ? `<label class="check">precision <select id="precision"><option value="vector" ${ui.precision === "vector" ? "selected" : ""}>exact vector</option><option value="raster" ${ui.precision === "raster" ? "selected" : ""}>grid raster</option></select></label>` : ""}
      </div>` : ""}
    </div>
    ${pendingPanel()}`;
}

function viewImage(sheet) {
  const images = ui.state.draft.previews.images;
  if (ui.view === "front" && images.assembled_front) return images.assembled_front;
  if (ui.view === "changes_base" && images.changes_vs_base) return images.changes_vs_base;
  if (ui.view === "changes_previous" && images.changes_vs_previous) return images.changes_vs_previous;
  return sheet ? sheet.image : "";
}

function pendingPanel() {
  if (!ui.pending) return "";
  return `<div class="pending" data-testid="pending-edit">
    <div class="spread"><strong>Pending edit: ${esc(describeOp(ui.pending))}</strong>
      <div class="row"><button class="small ghost" data-action="cancel-pending">Cancel</button><button class="small primary" data-action="apply-pending" ${busy() ? "disabled" : ""}>Apply edit</button></div></div>
    <details><summary>Edit request (L2 format)</summary><pre class="mono small">${esc(JSON.stringify(ui.pending, null, 2))}</pre></details>
  </div>`;
}

function describeOp(op) {
  const at = op.at_mm ? ` at (${op.at_mm.join(", ")}) mm` : "";
  switch (op.op) {
    case "fill_hole": return `fill the cutout of ${op.sheet}${at}`;
    case "remove_island": return `remove the island of ${op.sheet}${at}`;
    case "split": return `split the region at (${op.region.region_at_mm.join(", ")}) mm into a new sheet`;
    case "add": case "subtract": {
      const s = op.shape;
      const what = s.type === "circle" ? `circle r=${s.radius_mm} mm` : s.type === "bar" ? `slot ${s.width_mm} mm wide` : "rectangle";
      return `${op.op === "add" ? "add" : "cut"} ${what} on ${op.sheet} (${op.precision || "vector"})`;
    }
    case "bridge": return `bridge ${op.width_mm} mm wide on ${op.sheet}`;
    default: return op.op;
  }
}

function validationPanel() {
  const v = ui.state.draft.validation;
  const status = ui.state.draft.status;
  const ok = status.printable;
  const hints = v.failures.map((f) => {
    let hint = "";
    if (/reconstruction differs/.test(f)) hint = "The front view no longer matches the reviewed artwork. Undo, or use “Accept front view as artwork” if the visible change is intended.";
    else if (/sheet checks failed/.test(f)) hint = "See the sheet's checks below: every sheet must stay one 4-connected piece with material at least 1.2 mm wide and a complete perimeter.";
    else if (/visible masks disagree/.test(f)) hint = "Visible regions do not match the front view; undo the last edit.";
    return `<li><strong>${esc(f)}</strong>${hint ? `<br><span class="muted">${esc(hint)}</span>` : ""}</li>`;
  }).join("");
  const rows = ui.state.draft.sheets.slice().reverse().map((s) => {
    const c = s.checks;
    const full = v.details_recomputed;
    return `<tr class="${s.id === ui.sheet ? "selected" : ""}">
      <td>${swatch(s.color)} ${esc(s.export_name)}</td><td>${yesno(c.pass)}</td>
      <td>${full ? yesno(c.four_neighbor) : "✓"}</td><td>${full ? yesno(c.width_status) : "✓"}</td>
      <td>${full ? yesno(c.shared_perimeter_complete) : "✓"}</td><td>${full ? yesno(c.visible_inside_retained) : "✓"}</td>
      <td>${s.islands ? `<span class="no">${s.islands}</span>` : "0"}</td>
      <td>${s.vector_checks ? yesno(s.vector_checks.pass) : '<span class="muted">raster</span>'}</td></tr>`;
  }).join("");
  const islands = ui.state.draft.islands.map((i) => `<li>${esc(i.sheet)}: ${fmt(i.area_mm2)} mm² at (${i.point_on_island_mm.join(", ")}) mm${i.visible_px ? `, ${i.visible_px} px visible` : ", hidden"}
      <button class="small" data-action="locate" data-sheet="${esc(i.sheet)}" data-x="${i.point_on_island_mm[0]}" data-y="${i.point_on_island_mm[1]}">Locate</button>
      <button class="small" data-action="propose-op" data-op='${esc(JSON.stringify({ op: "remove_island", sheet: i.sheet, at_mm: i.point_on_island_mm }))}'>Remove…</button></li>`).join("");
  const summary = ok
    ? `front view ${status.reconstruction_vs_reviewed_target_px === 0 ? "identical to the reviewed artwork" : `${fmt(status.reconstruction_vs_reviewed_target_px)} px differ`} · ${fmt(status.floating_islands)} floating islands`
    : `${v.failures.length} failure${v.failures.length === 1 ? "" : "s"} — not exportable until fixed`;
  // Problems keep the section open; a valid draft collapses to its one-line summary.
  return `<details class="panel tight section" data-testid="validation-panel" data-section="validation" ${openAttr("validation", false, !ok || Boolean(islands))}>
    <summary><h3>Validation ${badge(ok ? "ok" : "bad", ok ? "REVIEWED_VALID" : "DRAFT_INVALID")}</h3><span class="small muted">${esc(summary)}</span></summary>
    <div class="section-body">
      <dl class="kv">
        <dt>Front view vs reviewed artwork</dt><dd>${status.reconstruction_vs_reviewed_target_px === 0 ? '<span class="yes">identical</span>' : `<span class="no">${fmt(status.reconstruction_vs_reviewed_target_px)} px differ</span>`}</dd>
        <dt>Deviation from source artwork</dt><dd>${fmt(status.deviation_from_source_artwork_px)} px ${status.deviation_from_source_artwork_px ? `<span class="muted">(${status.reconstruction_vs_reviewed_target_px ? "visible changes not accepted yet" : "accepted visible changes"})</span>` : ""}</dd>
        <dt>Floating islands</dt><dd>${fmt(status.floating_islands)}</dd>
        <dt>Repeated colors</dt><dd>${(v.repeated_colors || []).map((h) => swatch(h)).join(" ") || "none"}</dd>
        <dt>Validators</dt><dd class="muted">approved L0 validators · ${fmt(v.mm_per_pixel, 4)} mm/px</dd>
      </dl>
      ${hints ? `<h4 style="margin-top:10px">Failures</h4><ul class="small">${hints}</ul>` : ""}
      ${islands ? `<h4 style="margin-top:10px">Islands (must be bridged or removed)</h4><ul class="small">${islands}</ul>` : ""}
      <details data-section="per-sheet" ${openAttr("per-sheet", false, !ok)}><summary>Per-sheet checks${v.details_recomputed ? "" : " (all gate checks passed in the edit's validation)"}</summary>
        <div class="table-wrap"><table><thead><tr><th>Sheet (front first)</th><th>Pass</th><th>4-connected</th><th>Width ≥ 1.2 mm</th><th>Perimeter</th><th>Visible ⊂ material</th><th>Islands</th><th>Exact geometry</th></tr></thead><tbody>${rows}</tbody></table></div>
      </details>
    </div>
  </details>`;
}

function changePanel() {
  const c = ui.state.draft.last_change;
  if (!c) return "";
  if (c.action !== "edit") {
    return `<div class="panel tight section-flat"><h3 style="margin:0">Last action <span class="small muted" style="font-weight:400">${esc(c.action)}: ${c.edits_before} → ${c.edits_after} edits (replayed from the base). Compare with “Changes vs previous”.</span></h3></div>`;
  }
  const [cls, label, text] = CHANGE_KINDS[c.change.kind] || ["neutral", c.change.kind, ""];
  const ch = c.change;
  return `<details class="panel tight section" data-testid="change-panel" data-section="change" ${openAttr("change", false, cls === "bad")}>
    <summary><h3>Edit #${c.index}: ${esc(c.op.op)} ${badge(cls, label)}</h3>${badge(c.validation_status === "PASS" ? "ok" : "bad", c.validation_status)}</summary>
    <div class="section-body">
      <p class="small">${esc(text)}</p>
      <dl class="kv small">
        <dt>Visible pixels changed</dt><dd>${fmt(ch.visible_changed_px)}</dd>
        <dt>Material changed (px)</dt><dd>${Object.entries(ch.retained_changed_px || {}).map(([k, n]) => `${esc(k)}: ${n}`).join(", ") || "none"}</dd>
        ${Object.keys(ch.exact_geometry_changed_mm2 || {}).length ? `<dt>Exact geometry changed</dt><dd>${Object.entries(ch.exact_geometry_changed_mm2).map(([k, n]) => `${esc(k)}: ${fmt(n, 3)} mm²`).join(", ")}</dd>` : ""}
        ${ch.parameters_changed?.length ? `<dt>Parameters</dt><dd>${ch.parameters_changed.map(esc).join(", ")}</dd>` : ""}
        ${ch.sheets_added?.length ? `<dt>Sheets added</dt><dd>${ch.sheets_added.map(esc).join(", ")}</dd>` : ""}
        ${ch.sheets_removed?.length ? `<dt>Sheets removed</dt><dd>${ch.sheets_removed.map(esc).join(", ")}</dd>` : ""}
        ${ch.order_changed ? "<dt>Order</dt><dd>changed</dd>" : ""}
      </dl>
    </div>
  </details>`;
}

function previewsPanel() {
  const d = ui.state.draft;
  const images = d.previews.images;
  return `<details class="panel tight section" data-section="previews" ${openAttr("previews")}>
    <summary><h3>More previews</h3><span class="small muted">exploded stack and artwork comparison · ${esc(d.previews.mask_source || "")}</span></summary>
    <div class="section-body">
      <div class="gallery">
        ${images.exploded ? `<figure><img class="img" src="${esc(images.exploded)}" alt="exploded" loading="lazy"><figcaption>Exploded stack (back to front)</figcaption></figure>` : ""}
      </div>
      ${images.source_comparison ? `<figure style="margin-top:12px"><img class="img" src="${esc(images.source_comparison)}" alt="comparison" loading="lazy"><figcaption>Reviewed artwork | assembled | mismatches in red · click any image to open it full size</figcaption></figure>` : ""}
    </div>
  </details>`;
}

function advancedPanel() {
  const dis = busy() ? "disabled" : "";
  return `<details class="panel tight section" data-section="advanced" ${openAttr("advanced")}>
    <summary><h3>Advanced</h3><span class="small muted">raw edit request · reload from disk</span></summary>
    <div class="section-body stack">
      <p class="small muted">Every edit is saved immediately with its validation result. Invalid drafts are kept so you can fix them, but they cannot be exported.</p>
      <div><textarea id="raw-op" spellcheck="false" placeholder='{"op": "recolor", "sheet": "…", "color": "#AABBCC"}'></textarea>
        <p class="small muted">Operations: ${(ui.info?.edit_ops || []).map((o) => `<code>${esc(o)}</code>`).join(" ")}</p>
        <button class="small" data-action="raw-op" ${dis}>Review request</button></div>
      <div><button class="small ghost" data-action="reload">Reload from disk</button></div>
    </div>
  </details>`;
}

function sheetPanel(sheet) {
  const sheets = ui.state.draft.sheets;
  const index = sheets.findIndex((s) => s.id === sheet.id);
  const fromFront = sheets.length - 1 - index;
  const others = sheets.filter((s) => s.id !== sheet.id);
  const dis = busy() ? "disabled" : "";
  const fails = sheet.checks.pass === false;
  const rimKey = (s) => (s.rim ? `${s.rim.width_mm}x${s.rim.total_height_mm}` : "");
  const rimOnAll = Boolean(sheet.rim) && sheets.every((s) => rimKey(s) === rimKey(sheet));
  return `<div class="panel tight inspector" data-testid="sheet-panel">
    <div class="inspector-head">
      ${swatch(sheet.color, true)}
      <div><h3 style="margin:0">${esc(sheet.export_name)}</h3>
        <div class="small muted">Layer ${fromFront + 1} of ${sheets.length} from the front · ${esc(sheet.color)}</div></div>
      ${fails ? badge("bad", "fails") : badge("ok", "ok")}
    </div>
    <button class="thumb ${ui.view === "sheet" ? "active" : ""}" data-action="view" data-view="sheet" title="Show this layer on the canvas">
      <img src="${esc(sheet.image)}" alt="layer ${esc(sheet.export_name)}" draggable="false"></button>
    <div class="facts">
      <div><span>${fmt(sheet.thickness_mm)} mm</span>thickness</div>
      <div><span>${sheet.cutout_holes}</span>cutouts</div>
      <div><span class="${sheet.islands ? "no" : ""}">${sheet.islands}</span>islands</div>
      <div><span>${fmt(sheet.retained_fraction * 100, 0)} %</span>material</div>
    </div>
    <div class="inspector-section">
      <h4>Appearance</h4>
      <div class="field-row"><label for="p-color">Color</label><input type="color" id="p-color" value="${esc(sheet.color.toLowerCase())}"><button class="small" data-action="recolor" ${dis}>Recolor</button></div>
      <div class="field-row"><label for="p-thick">Thickness</label><span><input type="number" id="p-thick" value="${sheet.thickness_mm}" min="0.2" step="0.2"> mm</span><button class="small" data-action="thickness" ${dis}>Set</button></div>
    </div>
    <div class="inspector-section">
      <h4>Order</h4>
      <div class="row">
        <button class="small" data-action="move" data-to="${index + 1}" ${index >= sheets.length - 1 ? "disabled" : dis}>↑ Toward front</button>
        <button class="small" data-action="move" data-to="${index - 1}" ${index <= 0 ? "disabled" : dis}>↓ Toward back</button>
      </div>
    </div>
    <details class="inspector-section" data-section="rim" ${openAttr("rim", Boolean(sheet.rim))}>
      <summary><h4>Optional rim</h4><span class="small muted">${sheet.rim ? `${fmt(sheet.rim.width_mm)} × ${fmt(sheet.rim.total_height_mm)} mm${rimOnAll ? " · all layers" : ""}` : "off"}</span></summary>
      <label class="check"><input type="checkbox" id="p-rim" ${sheet.rim ? "checked" : ""}> Raised border</label>
      <label class="check"><input type="checkbox" id="p-rim-all" data-testid="rim-all" ${ui.rimAll ? "checked" : ""}> Apply to all ${sheets.length} layers</label>
      <p class="small muted" style="margin:4px 0">On the shared 12.5 mm perimeter band. Off by default; never required. With "all layers" the setting is one edit (one undo step).</p>
      <div class="field-row"><label for="p-rim-w">Width</label><span><input type="number" id="p-rim-w" value="${sheet.rim ? sheet.rim.width_mm : 6}" min="0.5" max="12.5" step="0.5"> mm</span></div>
      <div class="field-row"><label for="p-rim-h">Total height</label><span><input type="number" id="p-rim-h" value="${sheet.rim ? sheet.rim.total_height_mm : r3(Math.max(...sheets.map((s) => s.thickness_mm)) + 0.8)}" min="0.2" step="0.2"> mm</span></div>
      <button class="small" data-action="rim" ${dis}>Apply rim setting</button>
    </details>
    <details class="inspector-section" data-section="merge" ${openAttr("merge")}>
      <summary><h4>Merge</h4><span class="small muted">combine another layer into this one</span></summary>
      <div class="field-row"><select id="p-merge" aria-label="Layer to merge">${others.map((s) => `<option value="${esc(s.id)}">${esc(s.export_name)}</option>`).join("")}</select>
        <button class="small" data-action="merge" ${others.length ? dis : "disabled"}>Merge</button></div>
    </details>
    <details class="inspector-section" data-section="details" ${openAttr("details")}>
      <summary><h4>Details</h4></summary>
      <dl class="kv small">
        <dt>Stable id</dt><dd><code>${esc(sheet.id)}</code></dd>
        <dt>Order</dt><dd>${index} from back · ${fromFront} from front</dd>
        <dt>Assembly z</dt><dd>${fmt(sheet.assembly_z_mm, 3)} mm</dd>
        <dt>Geometry</dt><dd>${esc(sheet.geometry)}</dd>
        <dt>Material</dt><dd>${fmt(sheet.retained_fraction * 100, 1)} % of the sheet</dd>
        <dt>Visible</dt><dd>${fmt(sheet.visible_px)} px</dd>
        <dt>Cutouts / islands</dt><dd>${sheet.cutout_holes} / ${sheet.islands}</dd>
      </dl>
    </details>
  </div>`;
}

function historyPanel() {
  const d = ui.state.draft;
  const items = d.edits.slice().reverse().map((e) => {
    const [cls, label] = CHANGE_KINDS[e.kind] || ["neutral", e.kind];
    return `<li><span>#${e.index} <strong>${esc(e.op.op)}</strong> ${badge(cls, label)} ${e.validation_status !== "PASS" ? badge("bad", "invalid") : ""}</span>
      <button class="small ghost" data-action="revert" data-keep="${e.index}" ${busy() || e.index === d.edits.length ? "disabled" : ""} title="Keep edits 1..${e.index}">Revert here</button></li>`;
  }).join("");
  return `<details class="panel tight section" data-section="history" ${openAttr("history", d.edits.length > 0)}>
    <summary><h3>Edit history</h3><span class="small muted">${d.edits.length ? `${d.edits.length} edit${d.edits.length === 1 ? "" : "s"}` : "no edits yet"}</span></summary>
    <div class="section-body">
      <p class="small muted">Base: ${esc(d.base.source.split("/").slice(-2).join("/"))} (${esc(d.base.validation_status)})</p>
      ${items ? `<ul class="history">${items}</ul>` : ""}
      <button class="small ghost" data-action="revert" data-keep="0" ${d.edits.length && !busy() ? "" : "disabled"}>Revert to base</button>
    </div>
  </details>`;
}

// ------------------------------------------------------------------------------ canvas
function overlayGeometry() {
  const img = $("#edit-image");
  const canvas = $("#overlay");
  if (!img || !canvas) return null;
  const rect = img.getBoundingClientRect();
  const size = ui.state.draft.validation.outer_sheet_mm;
  return { img, canvas, rect, size };
}

function toMm(event) {
  const g = overlayGeometry();
  const x = ((event.clientX - g.rect.left) / g.rect.width) * g.size;
  const y = (1 - (event.clientY - g.rect.top) / g.rect.height) * g.size;
  return [r3(Math.min(Math.max(x, 0), g.size)), r3(Math.min(Math.max(y, 0), g.size))];
}

function toPx(point, g) {
  return [(point[0] / g.size) * g.canvas.width, (1 - point[1] / g.size) * g.canvas.height];
}

function drawOverlay() {
  const g = overlayGeometry();
  if (!g) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(g.rect.width * dpr));
  const h = Math.max(1, Math.round(g.rect.height * dpr));
  if (g.canvas.width !== w || g.canvas.height !== h) { g.canvas.width = w; g.canvas.height = h; }
  const ctx = g.canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  const mmToPx = w / g.size;
  ctx.lineWidth = 2 * dpr;
  const shape = ui.drag ? shapeFromDrag(ui.drag) : ui.pending;
  if (shape) drawShape(ctx, shape, g, mmToPx);
  const point = ui.pending?.at_mm || ui.pending?.region?.region_at_mm || ui.marker;
  if (point) {
    const [x, y] = toPx(point, g);
    ctx.strokeStyle = "#ff2d55";
    ctx.beginPath(); ctx.arc(x, y, 9 * dpr, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - 14 * dpr, y); ctx.lineTo(x + 14 * dpr, y); ctx.moveTo(x, y - 14 * dpr); ctx.lineTo(x, y + 14 * dpr); ctx.stroke();
  }
}

function drawShape(ctx, op, g, mmToPx) {
  const s = op.shape || (op.op === "bridge" ? { type: "bar", from_mm: op.from_mm, to_mm: op.to_mm, width_mm: op.width_mm } : null);
  if (!s) return;
  const adding = op.op === "add" || op.op === "bridge";
  ctx.strokeStyle = adding ? "#00a86b" : "#ff2d55";
  ctx.fillStyle = adding ? "rgba(0,168,107,0.25)" : "rgba(255,45,85,0.25)";
  ctx.beginPath();
  if (s.type === "circle") {
    const [x, y] = toPx(s.center_mm, g);
    ctx.arc(x, y, Math.max(s.radius_mm * mmToPx, 1), 0, Math.PI * 2);
  } else if (s.type === "polygon") {
    s.points_mm.forEach((p, i) => { const [x, y] = toPx(p, g); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.closePath();
  } else if (s.type === "bar") {
    const [x0, y0] = toPx(s.from_mm, g);
    const [x1, y1] = toPx(s.to_mm, g);
    const len = Math.hypot(x1 - x0, y1 - y0) || 1;
    const half = Math.max((s.width_mm * mmToPx) / 2, 1);
    const nx = (-(y1 - y0) / len) * half;
    const ny = ((x1 - x0) / len) * half;
    ctx.moveTo(x0 + nx, y0 + ny); ctx.lineTo(x1 + nx, y1 + ny); ctx.lineTo(x1 - nx, y1 - ny); ctx.lineTo(x0 - nx, y0 - ny);
    ctx.closePath();
  }
  ctx.fill();
  ctx.stroke();
}

function shapeFromDrag({ from, to }) {
  const sheet = ui.sheet;
  const precision = ui.precision;
  switch (ui.tool) {
    case "add_circle": case "subtract_circle":
      return { op: ui.tool.startsWith("add") ? "add" : "subtract", sheet, shape: { type: "circle", center_mm: from, radius_mm: r3(Math.hypot(to[0] - from[0], to[1] - from[1])) }, precision };
    case "add_rect": case "subtract_rect": {
      const [x0, x1] = [Math.min(from[0], to[0]), Math.max(from[0], to[0])];
      const [y0, y1] = [Math.min(from[1], to[1]), Math.max(from[1], to[1])];
      return { op: ui.tool.startsWith("add") ? "add" : "subtract", sheet, shape: { type: "polygon", points_mm: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]] }, precision };
    }
    case "subtract_bar":
      return { op: "subtract", sheet, shape: { type: "bar", from_mm: from, to_mm: to, width_mm: ui.barWidth }, precision };
    case "bridge":
      return { op: "bridge", sheet, from_mm: from, to_mm: to, width_mm: ui.barWidth, precision };
    default:
      return null;
  }
}

function bindCanvas() {
  const canvas = $("#overlay");
  if (!canvas) return;
  canvas.addEventListener("pointermove", (e) => {
    ui.hover = toMm(e);
    const c = $("#coords");
    if (c) c.textContent = `x ${ui.hover[0].toFixed(2)} mm · y ${ui.hover[1].toFixed(2)} mm`;
    if (ui.drag) { ui.drag.to = ui.hover; drawOverlay(); }
  });
  canvas.addEventListener("pointerdown", (e) => {
    if (busy() || !ui.sheet) return;
    const point = toMm(e);
    if (["add_circle", "subtract_circle", "add_rect", "subtract_rect", "subtract_bar", "bridge"].includes(ui.tool)) {
      canvas.setPointerCapture(e.pointerId);
      ui.drag = { from: point, to: point };
      ui.pending = null;
      drawOverlay();
    }
  });
  canvas.addEventListener("pointerup", (e) => {
    const point = toMm(e);
    if (ui.drag) {
      ui.drag.to = point;
      const op = shapeFromDrag(ui.drag);
      ui.drag = null;
      const degenerate = (op.shape?.radius_mm === 0) || (op.shape?.type === "polygon" && (op.shape.points_mm[0][0] === op.shape.points_mm[1][0] || op.shape.points_mm[0][1] === op.shape.points_mm[2][1]))
        || ((op.shape?.type === "bar" || op.op === "bridge") && JSON.stringify(op.from_mm || op.shape.from_mm) === JSON.stringify(op.to_mm || op.shape.to_mm));
      ui.pending = degenerate ? null : op;
      render();
      return;
    }
    if (busy() || !ui.sheet) return;
    if (ui.tool === "fill_hole" || ui.tool === "remove_island") ui.pending = { op: ui.tool, sheet: ui.sheet, at_mm: point };
    else if (ui.tool === "split") ui.pending = { op: "split", sheet: ui.sheet, region: { region_at_mm: point }, place: "front" };
    else { ui.marker = point; ui.pending = null; }
    render();
  });
  canvas.addEventListener("pointerleave", () => { ui.hover = null; });
  $("#edit-image")?.addEventListener("load", drawOverlay);
}

// ----------------------------------------------------------------------- printability
function processForm(prefix) {
  const presets = ui.info?.process_presets || [];
  const p = ui.process;
  return `<div class="subpanel stack" data-testid="process-form">
    <div class="spread"><h4 style="margin:0">Printer / process</h4>
      <label class="check small">Preset <select data-action="preset" id="${prefix}-preset">${presets.map((x, i) => `<option value="${i}" ${x.name === p.name && x.source === p.source ? "selected" : ""}>${esc(x.file || "built-in default")} (${esc(x.name)})</option>`).join("")}</select></label></div>
    <div class="form-grid">${PROCESS_FIELDS.map(([k, label]) => `<label class="field"><span>${esc(label)}</span><input type="number" step="any" min="0" data-process="${k}" value="${esc(p[k])}"></label>`).join("")}</div>
    <p class="small ${String(p.source).startsWith("ASSUMPTION") ? "" : "muted"}">${String(p.source).startsWith("ASSUMPTION") ? badge("warn", "assumption") + " " : ""}${esc(p.source)}</p>
  </div>`;
}

function processPayload() {
  const out = { name: ui.process.name, source: ui.process.source };
  for (const [k] of PROCESS_FIELDS) out[k] = Number(ui.process[k]);
  return out;
}

function findingsTable(findings, locate = true) {
  if (!findings.length) return '<p class="small">No RISK or UNSUPPORTED features for this process.</p>';
  return `<div class="table-wrap"><table data-testid="findings">
    <thead><tr><th>Severity</th><th>Sheet file</th><th>Feature</th><th>Width</th><th>Visible area</th><th>At (mm)</th><th>Note</th>${locate ? "<th></th>" : ""}</tr></thead>
    <tbody>${findings.map((f) => `<tr>
      <td>${badge(f.severity === "UNSUPPORTED" ? "bad" : "warn", f.severity)}</td><td>${esc(f.file)}</td><td>${esc(String(f.kind).replace(/_/g, " "))}</td>
      <td>${f.width_mm != null ? `${fmt(f.width_mm, 3)} mm` : "–"}</td><td>${f.visible_area_mm2 != null ? `${fmt(f.visible_area_mm2, 3)} mm²` : "–"}</td>
      <td class="mono">${f.at_mm ? esc(f.at_mm.join(", ")) : "–"}</td><td class="small">${esc(f.note)}</td>
      ${locate ? `<td>${f.at_mm ? `<button class="small" data-action="locate" data-sheet="${esc(f.sheet_id)}" data-x="${f.at_mm[0]}" data-y="${f.at_mm[1]}">Locate</button>` : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

function printabilityView() {
  const est = ui.state.printability;
  const valid = ui.state.draft.status.printable;
  const findings = est ? est.features.filter((f) => f.severity !== "INFO") : [];
  const info = est ? est.features.filter((f) => f.severity === "INFO") : [];
  return `<div class="panel stack">
    <div class="spread"><h2>FDM printability estimate</h2>${badge("outline", "estimate · not a slicer · not a print")}</div>
    <p class="muted">Measures every opening, slot, gap and thin material of the current draft's exact export geometry against the printer parameters below, plus build volume and layer quantization. Geometry is never changed: problems are reported for you to decide.</p>
    ${processForm("est")}
    <div class="row"><button class="primary" data-action="run-printability" ${busy() ? "disabled" : ""}>Run estimate on the current draft</button>
      ${valid ? "" : '<span class="small muted">The draft is invalid; the estimate still runs, but the kit export will refuse it.</span>'}</div>
  </div>
  ${est ? `<div class="panel stack" data-testid="printability-result">
    <div class="spread"><h3 style="margin:0">Result ${badge(est.status === "NO_RISKS_FOUND" ? "ok" : est.status === "RISKS_REPORTED" ? "warn" : "bad", fdmLabel(est.status))} ${est.stale ? badge("warn", "out of date: the draft changed") : ""}</h3>
      <span class="small muted">${esc(est.process.name)} · ${esc(est.checked_utc)}</span></div>
    <p class="small">${est.counts.UNSUPPORTED} unsupported · ${est.counts.RISK} risk · ${est.counts.INFO} info. ${esc(est.basis)}.</p>
    ${findings.length ? `<p class="small"><a href="#" data-action="tab" data-tab="proposal" data-testid="to-resize">Check whether a larger sheet clears these (Physical size) →</a></p>` : ""}
    ${findingsTable(findings)}
    ${info.length ? `<details><summary>${info.length} informational item(s)</summary>${infoList(info)}</details>` : ""}
    <h4>Maps <span class="legend"><span><i style="background:#ff8c00"></i>risk</span><span><i style="background:#ff0000"></i>unsupported</span></span></h4>
    <div class="gallery">${est.map_urls.map((u) => `<figure><img class="img" src="${esc(u)}" alt="map"><figcaption>${esc(u.split("/").pop().split("?")[0])}</figcaption></figure>`).join("")}</div>
    <p class="small muted">Physical result: ${badge("outline", "NOT VERIFIED")} — only your test print can confirm these thresholds.</p>
  </div>` : ""}`;
}

function infoList(items) {
  return `<ul class="small">${items.map((f) => `<li>${esc(f.file)} · ${esc(String(f.kind).replace(/_/g, " "))}${f.width_mm != null ? ` · ${fmt(f.width_mm, 3)} mm` : ""}: ${esc(f.note)}</li>`).join("")}</ul>`;
}

// --------------------------------------------------------------------------------- kit
function kitView() {
  const kits = ui.state.kits || [];
  const latest = kits[0];
  // With a kit, the creation form stays folded behind "+ New Print Kit" (top right) until asked for.
  const open = !latest || ui.kitBuildOpen;
  const needed = latest && (latest.stale || latest.status.cad !== "CAD_VALID");
  return `${kitNotice()}
    ${latest ? `<div class="kit-toolbar"><button class="${needed && !open ? "primary" : ""}" data-action="toggle-kit-build" data-testid="kit-new" aria-expanded="${open}">+ New Print Kit</button></div>` : ""}
    ${open ? kitBuildPanel(latest) : ""}
    ${latest ? kitCard(latest, true) : ""}
    ${kits.length > 1 ? `<section class="kit-earlier" data-testid="kit-earlier">
      <h3>Earlier kits <span class="muted small">(${kits.length - 1}) · each one can be downloaded or deleted on its own</span></h3>
      ${kits.slice(1).map((k) => kitCard(k, false)).join("")}
    </section>` : ""}`;
}

function kitNotice() {
  const n = ui.kitNotice;
  if (!n) return "";
  return `<div class="banner ${n.kind === "ok" ? "ok" : n.kind === "bad" ? "error" : "warn"} kit-notice" role="status" data-testid="kit-notice">
    <div class="spread"><strong>${esc(n.title)}</strong><button class="small ghost" data-action="dismiss-kit-notice">Dismiss</button></div>
    ${n.text ? `<p class="small">${esc(n.text)}</p>` : ""}
    ${n.items?.length ? `<ul class="small mono">${n.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
  </div>`;
}

function kitBuildPanel(latest) {
  const valid = ui.state.draft.status.printable;
  // The build form is the main action until a kit matches the current draft; then it folds away.
  const needed = !latest || latest.stale || latest.status.cad !== "CAD_VALID";
  return `<section class="panel kit-build" data-testid="kit-build">
    <div class="spread"><h3 style="margin:0">${latest ? "Build a new print kit" : "Build the print kit"}</h3>
      <span class="small muted">${latest ? (latest.stale ? "the draft changed after the latest kit" : "printer/process settings, optional Bambu Studio project") : "export STEP/STL for every sheet"}</span></div>
    <div class="stack" style="margin-top:12px">
      <p class="muted small">Exports one STEP and one STL per sheet with the approved CAD pipeline, reopens every file, compares artwork → reviewed stack → exported geometry → reopened CAD, runs the FDM estimate and writes a guide and checksums. No frame, housing or glue is added; the rim stays optional. A new kit never replaces an earlier one.</p>
      ${processForm("kit")}
      ${bambuOption()}
      <div class="row"><button class="${needed ? "primary" : ""}" data-action="build-kit" ${valid && !busy() ? "" : "disabled"}>${latest ? "Build new print kit" : "Build print kit"}</button>
        ${valid ? "" : '<span class="small no">The draft is invalid (see Review &amp; edit); invalid drafts are never exported.</span>'}</div>
    </div>
  </section>`;
}

function bambuOption() {
  const env = ui.info?.bambu;
  if (!env) return "";
  const printer = ui.bambuPrinter || env.default_printer;
  const found = env.profiles
    ? `Bambu Studio presets ${esc(env.profiles_version)} found${env.bambu_studio ? `; Bambu Studio ${esc(env.bambu_studio_version || "")} will load and slice the project to check it` : "; Bambu Studio itself not found, so the project cannot be checked here (experimental)"}.`
    : "Bambu Studio is not installed here: its presets are needed to write the project settings, so no project will be written.";
  return `<div class="subpanel stack" data-testid="bambu-option">
    <label class="check"><input type="checkbox" id="kit-bambu" data-testid="bambu-3mf" ${ui.bambu ? "checked" : ""}> Also write a Bambu Studio project (.3mf): one plate per sheet, in stack order</label>
    <label class="field"><span>Bambu printer</span><select id="kit-bambu-printer" ${ui.bambu ? "" : "disabled"}>${env.printers.map((p) => `<option value="${esc(p.preset)}" ${p.preset === printer ? "selected" : ""}>${esc(p.model)} (${fmt(p.bed_mm[0])} × ${fmt(p.bed_mm[1])} mm)</option>`).join("")}</select></label>
    <p class="small muted">${found} Sheets that do not fit the printer's printable area are reported and no project is written. No AMS is assumed: each plate uses one filament. STEP/STL files are unchanged.</p>
  </div>`;
}

// Kit headline: what the user can do with this kit right now.
function kitHeadline(kit) {
  if (kit.missing_files.length) return ["bad", "!", "Kit incomplete", "Some files of this kit are missing on disk (an earlier delete may have been interrupted). Delete it again or build a new kit."];
  if (kit.status.cad !== "CAD_VALID") return ["bad", "!", "Kit has CAD problems", "The exported files did not pass the CAD checks; do not print them."];
  if (kit.stale) return ["warn", "!", "Kit out of date", "The draft changed after this kit was built. Build a new kit before printing."];
  return ["ok", "✓", "Print Kit Ready", "STEP and STL files for every sheet, verified by reopening them. Download the kit and follow the assembly guide."];
}

function kitWarnings(kit) {
  const out = [];
  if (kit.stale) out.push(["warn", "Out of date: the draft changed after this kit was built."]);
  if (kit.status.cad !== "CAD_VALID") out.push(["bad", `CAD problems: ${kit.cad_problems.join("; ")}`]);
  if (kit.missing_files.length) out.push(["bad", `${kit.missing_files.length} sheet file(s) missing on disk.`]);
  const c = kit.printability.counts;
  if (c.UNSUPPORTED) out.push(["bad", `${c.UNSUPPORTED} unsupported printability feature(s) for process ${kit.printability.process.name} (see Validation & Printability).`]);
  if (c.RISK) out.push(["warn", `${c.RISK} printability risk(s) for process ${kit.printability.process.name} (see Validation & Printability).`]);
  const b = kit.bambu;
  if (b && b.status !== "WRITTEN") out.push(["warn", `Bambu Studio project not written: ${b.problems.join("; ")}`]);
  if (b && b.validation === "FAILED") out.push(["bad", `Bambu Studio check failed: ${(b.check_problems || []).join("; ")}`]);
  if (b && b.validation && b.validation.startsWith("EXPERIMENTAL")) out.push(["warn", "Bambu Studio project not checked by Bambu Studio on this computer (experimental)."]);
  return out;
}

function kitSection(kit, key, title, summary, body) {
  const id = `kit-${key}-${kit.id}`;
  return `<details class="kit-section section" data-section="${esc(id)}" data-testid="kit-section-${key}" ${openAttr(id)}>
    <summary><h3>${esc(title)}</h3><span class="small muted">${summary}</span></summary>
    <div class="section-body stack">${body}</div>
  </details>`;
}

function kitCard(kit, latest) {
  const [tone, icon, title, lead] = kitHeadline(kit);
  const ready = tone === "ok";
  const created = kit.created_utc ? new Date(kit.created_utc).toLocaleString() : "";
  const facts = [esc(kit.id), created && esc(created), `${kit.assembly.length} sheets`, `${fmt(kit.total_stack_height_mm)} mm stack`, `process ${esc(kit.process_name)}`].filter(Boolean).join(" · ");
  const warnings = kitWarnings(kit);
  const b = kit.bambu;
  const confirming = ui.confirmDelete === kit.id;
  const card = `
    <div class="kit-hero ${tone}" data-testid="kit-hero">
      <div class="kit-hero-text">
        <div class="kit-hero-icon" aria-hidden="true">${icon}</div>
        <div><h2 data-testid="kit-title">${esc(title)}</h2>
          <p class="small">${esc(lead)}</p>
          <p class="small muted kit-facts">${facts}</p></div>
      </div>
      <div class="kit-actions" data-testid="kit-actions">
        <a class="button ${ready && latest ? "primary large" : ""}" href="${esc(kit.zip_url)}" download data-testid="kit-download">⬇ Download Print Kit (.zip)</a>
        <a class="button" href="${esc(kit.guide_url)}" target="_blank" rel="noopener" data-testid="kit-guide">Assembly Guide</a>
        ${b && b.status === "WRITTEN" ? `<a class="button" href="${esc(b.url)}" download data-testid="kit-bambu-download">Bambu Studio .3mf</a>` : ""}
        ${ui.info?.can_open_folders ? `<button data-action="open-kit" data-kit="${esc(kit.id)}" data-testid="kit-open-folder">Open Kit Folder</button>` : ""}
      </div>
    </div>
    ${warnings.length ? `<ul class="kit-warnings" data-testid="kit-warnings">${warnings.map(([cls, text]) => `<li class="${cls}">${badge(cls, cls === "bad" ? "problem" : "warning")} ${esc(text)}</li>`).join("")}</ul>` : ""}
    <div class="grid3 kit-status">
      ${statusCard("CAD (reopened files)", kit.status.cad === "CAD_VALID" ? ["ok", "CAD_VALID", "Every STEP is one valid solid, every STL one watertight part; volumes and front view match."] : ["bad", "CAD_INVALID", kit.cad_problems.join("; ")], "kit-cad")}
      ${statusCard("FDM estimate", [kit.status.fdm_estimate === "NO_RISKS_FOUND" ? "ok" : kit.status.fdm_estimate === "RISKS_REPORTED" ? "warn" : "bad", fdmLabel(kit.status.fdm_estimate), `${kit.printability.counts.UNSUPPORTED} unsupported · ${kit.printability.counts.RISK} risk · process ${kit.printability.process.name}`], "kit-fdm")}
      <div class="status-card neutral physical" data-testid="kit-physical"><div class="label">Physical</div><div class="value">${badge("outline", kit.status.physical === "NOT_VERIFIED" ? "NOT VERIFIED" : kit.status.physical)}</div><div class="note">Print and stack the kit, then report the result (L-G10).</div></div>
    </div>
    <div class="kit-sections">
      ${kitFilesSection(kit)}
      ${kitAssemblySection(kit)}
      ${kitValidationSection(kit)}
      ${kitTechnicalSection(kit)}
    </div>
    <div class="kit-footer">
      ${confirming ? `<div class="kit-confirm" role="alertdialog" aria-label="Confirm kit deletion" data-testid="kit-confirm-delete">
          <p><strong>Delete ${esc(kit.id)}?</strong> This permanently removes its folder <code>${esc(kit.folder)}</code> from disk: the STEP/STL files, previews, guide, checksums${b && b.status === "WRITTEN" ? " and the Bambu Studio project" : ""}. Downloaded copies are not affected.</p>
          <p class="small muted">The source image, the reviewed project, the printability estimate and other kits are not touched.</p>
          <div class="row"><button class="danger-fill" data-action="delete-kit-confirmed" data-kit="${esc(kit.id)}" data-testid="kit-delete-confirm">Delete kit permanently</button>
            <button data-action="delete-kit-cancel" data-testid="kit-delete-cancel">Cancel</button></div>
        </div>`
        : `<button class="small ghost danger" data-action="delete-kit" data-kit="${esc(kit.id)}" data-testid="kit-delete" ${busy() ? 'disabled title="Wait for the running job to finish"' : ""}>Delete kit…</button>`}
    </div>`;
  if (latest) return `<section class="panel kit-card" data-testid="kit-card" data-kit="${esc(kit.id)}"><div class="kit-body">${card}</div></section>`;
  const key = `kit-older-${kit.id}`;
  return `<details class="panel tight section kit-card kit-older" data-testid="kit-card" data-kit="${esc(kit.id)}" data-section="${esc(key)}" ${openAttr(key, false, confirming)}>
    <summary><h3>${esc(kit.id)}</h3>${badge(tone, title)}<span class="small muted">${esc(created)}</span></summary>
    <div class="section-body kit-body">${card}</div>
  </details>`;
}

function kitFilesSection(kit) {
  const b = kit.bambu;
  const rows = kit.assembly.map((r) => `<tr><td>${String(r.order_back_to_front).padStart(2, "0")}</td>
    <td>${swatch(r.color_hex)} <span class="mono">${esc(r.file_stl.split("/").pop().replace(/\.stl$/, ""))}</span></td>
    <td><a href="${esc(`${kit.base_url}/${r.file_stl}`)}" download>STL</a> · <a href="${esc(`${kit.base_url}/${r.file_step}`)}" download>STEP</a></td></tr>`).join("");
  let bambu = `<p class="small muted">Bambu Studio project: ${esc(kit.not_included?.["3mf"] || "not requested for this kit")}</p>`;
  if (b && b.status !== "WRITTEN") bambu = `<p class="small no" data-testid="bambu-result">Bambu Studio project not written: ${esc(b.problems.join("; "))}</p>`;
  if (b && b.status === "WRITTEN") {
    const level = b.validation === "BAMBU_STUDIO_CLI_VERIFIED" ? badge("ok", "loaded and sliced by Bambu Studio")
      : b.validation === "FAILED" ? badge("bad", "check failed") : badge("warn", "experimental: not checked by Bambu Studio");
    bambu = `<div class="subpanel stack"><div class="row" data-testid="bambu-result"><strong>Bambu Studio project</strong> <a class="button small" href="${esc(b.url)}" download>Download .3mf</a> ${level}</div>
      <p class="small muted">${esc(b.printer)} · ${b.plates.length} plates (plate 1 = back) · ${esc(b.process_preset)}${b.check_problems.length ? ` · ${esc(b.check_problems.join("; "))}` : ""}. Filament slots carry the sheet colors; AMS slots are chosen when you print.</p>
      <div class="table-wrap"><table><thead><tr><th>Plate</th><th>Sheet</th><th>Filament</th></tr></thead><tbody>
        ${b.plates.map((p) => `<tr><td>${p.plate}</td><td class="mono">${esc(p.plate_name)}</td><td>${swatch(p.color_hex)} slot ${p.filament_slot} · ${esc(p.color_hex)}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  }
  const summary = `${kit.assembly.length} STEP + ${kit.assembly.length} STL · zip${b && b.status === "WRITTEN" ? " · Bambu .3mf" : ""}`;
  return kitSection(kit, "files", "Files & Exports", summary, `
    <div class="row"><a class="button" href="${esc(kit.zip_url)}" download>Whole kit (.zip)</a><a class="button" href="${esc(kit.guide_url)}" target="_blank" rel="noopener">PRINT_AND_STACK_GUIDE.md</a></div>
    <div class="table-wrap"><table data-testid="kit-files"><thead><tr><th>Order</th><th>Sheet</th><th>Files</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted">STL carries no color: load the filament listed for each sheet (Assembly &amp; Layers). Print every file flat as exported.</p>
    ${bambu}`);
}

function kitAssemblySection(kit) {
  const colors = Object.keys(kit.filament_by_color).length;
  const summary = `${kit.assembly.length} sheets · ${colors} color${colors === 1 ? "" : "s"} · ${fmt(kit.total_stack_height_mm)} mm total`;
  return kitSection(kit, "assembly", "Assembly & Layers", summary, `
    <div class="table-wrap"><table data-testid="kit-assembly">
      <thead><tr><th>Order (back = 00)</th><th>File</th><th>Color</th><th>Height</th><th>Rim</th><th>Assembly z</th><th>Filament</th></tr></thead>
      <tbody>${kit.assembly.map((r) => `<tr><td>${String(r.order_back_to_front).padStart(2, "0")}</td><td class="mono">${esc(r.file_stl.split("/").pop())}</td>
        <td>${swatch(r.color_hex)} ${esc(r.color_hex)}</td><td>${fmt(r.stack_height_mm)} mm</td><td>${r.rim ? `${fmt(r.rim.width_mm)} × ${fmt(r.rim.total_height_mm)} mm` : "none"}</td>
        <td>${fmt(r.assembly_z0_mm, 2)}–${fmt(r.assembly_z1_mm, 2)} mm</td><td>${fmt(r.estimated_filament_g)} g</td></tr>`).join("")}</tbody></table></div>
    <p class="small muted">Filament by color: ${Object.entries(kit.filament_by_color).map(([hex, v]) => `${swatch(hex)} ${fmt(v.estimated_g)} g`).join(" · ")} (estimate, ${fmt(kit.printability.process.filament_density_g_cm3)} g/cm³).</p>
    <div class="gallery">
      ${kit.images.assembled_front ? `<figure><img class="img" src="${esc(kit.images.assembled_front)}" alt="front" loading="lazy"><figcaption>Front view from reopened STL</figcaption></figure>` : ""}
      ${kit.images.exploded_labelled ? `<figure><img class="img" src="${esc(kit.images.exploded_labelled)}" alt="exploded" loading="lazy"><figcaption>Labelled exploded view</figcaption></figure>` : ""}
    </div>
    ${kit.images.assembled_side ? `<figure><img class="img" src="${esc(kit.images.assembled_side)}" alt="side section" loading="lazy"><figcaption>True-scale side section from the reopened STL (full panel and detail) · click any image to open it full size</figcaption></figure>` : ""}`);
}

function kitValidationSection(kit) {
  const cmp = kit.comparisons;
  const vol = Object.values(cmp.exported_vs_reopened_cad);
  const maxVol = Math.max(...vol.map((r) => Math.abs(r.step_volume_vs_expected_pct)), ...vol.map((r) => Math.abs(r.stl_volume_vs_expected_pct)));
  const c = kit.printability.counts;
  const summary = `${esc(kit.status.cad)} · ${c.UNSUPPORTED} unsupported · ${c.RISK} risk · ${fmt(cmp.approved_artwork_vs_reopened_cad_front_view.mismatched_px)} px front-view difference`;
  return kitSection(kit, "validation", "Validation & Printability", summary, `
    <dl class="kv small">
      <dt>Artwork vs reviewed stack</dt><dd>${fmt(cmp.approved_artwork_vs_reviewed_stack.front_view_mismatched_px)} px differ</dd>
      <dt>Artwork vs reopened CAD front view</dt><dd>${fmt(cmp.approved_artwork_vs_reopened_cad_front_view.mismatched_px)} px differ · stack mismatch ${fmt(cmp.approved_artwork_vs_reopened_cad_front_view.stack_mismatch_px)} px</dd>
      <dt>Max STEP/STL volume error</dt><dd>${fmt(maxVol, 4)} %</dd>
      <dt>Total stack height</dt><dd>${fmt(kit.total_stack_height_mm)} mm</dd>
    </dl>
    <h4>Printability findings (estimate, process ${esc(kit.printability.process.name)})</h4>
    ${findingsTable(kit.printability.findings, false)}
    ${kit.maps.length ? `<details data-section="kit-maps-${esc(kit.id)}" ${openAttr(`kit-maps-${kit.id}`)}><summary>Printability maps (${kit.maps.length}) · orange = risk, red = unsupported</summary>
      <div class="gallery">${kit.maps.map((u) => `<figure><img class="img" src="${esc(u)}" alt="map" loading="lazy"><figcaption>${esc(u.split("/").pop())}</figcaption></figure>`).join("")}</div></details>` : ""}
    <p class="small muted">Physical result: ${badge("outline", "NOT VERIFIED")} — only your test print can confirm these thresholds.</p>`);
}

function kitTechnicalSection(kit) {
  const check = ui.kitChecks[kit.id];
  const p = kit.printability.process;
  const b = kit.bambu;
  const notes = Object.entries(kit.not_included || {}).filter(([k]) => !(k === "3mf" && b && b.status === "WRITTEN"));
  return kitSection(kit, "technical", "Technical details", "print_kit.json · checksums · process values", `
    <div class="row"><a class="button small" href="${esc(kit.manifest_url)}" target="_blank" rel="noopener" data-testid="kit-manifest">print_kit.json</a>
      <button class="small" data-action="verify-kit" data-kit="${esc(kit.id)}" data-testid="kit-verify">Verify checksums</button></div>
    ${check ? `<p class="small" data-testid="kit-verify-result">${check.checksums_ok ? '<span class="yes">✓</span> All files match checksums.sha256.' : `<span class="no">✗</span> Changed or missing: ${esc(check.changed_or_missing.join(", "))}`} <span class="muted">(${esc(check.checked_utc)})</span></p>` : ""}
    <dl class="kv small">
      <dt>Kit id</dt><dd class="mono">${esc(kit.id)}</dd>
      <dt>Folder</dt><dd class="mono">${esc(ui.info?.workspace ? `${ui.info.workspace}/${kit.folder}` : kit.folder)}</dd>
      <dt>Process</dt><dd>${esc(p.name)} · ${esc(p.source)}</dd>
      <dt>Process values</dt><dd class="mono">nozzle ${fmt(p.nozzle_diameter_mm)} · line ${fmt(p.line_width_mm)} · layer ${fmt(p.layer_height_mm)} / first ${fmt(p.first_layer_height_mm)} · min opening ${fmt(p.min_opening_mm)} · min material ${fmt(p.min_positive_feature_mm)} · bed ${fmt(p.bed_x_mm)} × ${fmt(p.bed_y_mm)} × ${fmt(p.max_z_mm)} mm</dd>
      ${b && b.status === "WRITTEN" ? `<dt>Bambu Studio check</dt><dd>${esc(b.check_status || "not run")}${b.check_tool ? ` · ${esc(b.check_tool)}` : ""}${b.notes?.length ? ` · ${esc(b.notes.join("; "))}` : ""}</dd>` : ""}
      ${notes.map(([k, v]) => `<dt>Not included: ${esc(k)}</dt><dd class="muted">${esc(v)}</dd>`).join("")}
    </dl>`);
}

// -------------------------------------------------------------------------- actions
function projectDeleteConfirm(name) {
  const folder = ui.info ? `${ui.info.workspace}/${name}` : name;
  return `<div class="kit-confirm" role="alertdialog" aria-label="Confirm project deletion" data-testid="project-confirm-delete">
    <p><strong>Delete project “${esc(name)}” permanently?</strong></p>
    <p class="small">This removes the project folder <code>${esc(folder)}</code> and everything in it: the copy of the image made at import, proposals, previous attempts, the review and its edit history, printability estimates and print kits. It cannot be undone. The original image file you imported from elsewhere on your computer is not touched.</p>
    <div class="row"><button class="danger-fill" data-action="delete-project-confirmed" data-project="${esc(name)}" data-testid="project-delete-confirm">Delete project permanently</button>
      <button data-action="delete-project-cancel" data-testid="project-delete-cancel">Cancel</button></div>
  </div>`;
}

function projectNotice() {
  const n = ui.projectNotice;
  if (!n) return "";
  return `<div class="banner ${n.kind === "ok" ? "ok" : n.kind === "bad" ? "error" : "warn"}" role="status" data-testid="project-notice">
    <div class="spread"><strong>${esc(n.title)}</strong><button class="small ghost" data-action="dismiss-project-notice">Dismiss</button></div>
    ${n.text ? `<p class="small">${esc(n.text)}</p>` : ""}
    ${n.items?.length ? `<ul class="small mono">${n.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
  </div>`;
}

// Delete a whole project, then go back to the project list (also after a partial failure, which
// keeps the project listed so it can be deleted again).
async function deleteProject(name) {
  ui.confirmProject = null;
  let result;
  try {
    result = await api("POST", `/api/projects/${encodeURIComponent(name)}/delete`, {});
  } catch (err) {
    if (ui.name === name) { showError(err, `Could not delete project ${name}`); return; }
    ui.projectNotice = { kind: "bad", title: `Could not delete project ${name}`, text: err.message };
    await loadProjects();
    render();
    return;
  }
  ui.projectNotice = result.status === "DELETED"
    ? { kind: "ok", title: `Deleted project ${name}`, text: `${result.removed_files} file(s) removed from disk. ${result.notes.join(" ")}` }
    : { kind: "warn", title: `Project ${name} was only partly deleted`,
        text: `${result.removed_files} file(s) removed, ${result.failed_count} could not be (open in another program or no permission). The project stays listed; close those files and delete it again.`,
        items: result.failed.map((f) => `${f.path}: ${f.error}`) };
  if (ui.name === name) {  // back to the project list
    clearTimeout(pollTimer);
    ui.job = null;
    ui.name = null;
    ui.state = null;
    ui.error = null;
    history.replaceState(null, "", "#/");
  }
  await loadProjects();
  render();
}

async function deleteKit(kitId) {
  ui.confirmDelete = null;
  try {
    const result = await api("POST", `/api/projects/${encodeURIComponent(ui.name)}/kits/${encodeURIComponent(kitId)}/delete`, {});
    delete ui.kitChecks[kitId];
    ui.kitNotice = result.status === "DELETED"
      ? { kind: "ok", title: `Deleted ${kitId}`, text: [`${result.removed_files} file(s) removed from disk.`, ...result.notes].join(" ") }
      : { kind: "warn", title: `${kitId} was only partly deleted`,
          text: `${result.removed_files} file(s) removed, ${result.failed_count} could not be (open in another program or no permission). Close them and delete the kit again.`,
          items: result.failed.map((f) => `${f.path}: ${f.error}`) };
  } catch (err) {
    ui.kitNotice = { kind: "bad", title: `Could not delete ${kitId}`, text: err.message };
  }
  await loadProjects();
  await loadState();
  render();
}

function editOp(op, label) {
  return submit(`/api/projects/${encodeURIComponent(ui.name)}/edit`, { op }, label);
}

async function onClick(event) {
  if (event.target.matches("img.img")) {
    window.open(event.target.src, "_blank", "noopener");
    return;
  }
  const el = event.target.closest("[data-action]");
  if (!el || el.tagName === "SELECT") return;
  const a = el.dataset.action;
  const project = `/api/projects/${encodeURIComponent(ui.name)}`;
  const sheet = ui.sheet;
  switch (a) {
    case "tab": event.preventDefault(); setTab(el.dataset.tab); render(); break;
    case "dismiss-error": ui.error = null; render(); break;
    case "create": createProject(); break;
    case "propose": submit(`${project}/propose`, {}); break;
    case "apply-details": {
      const p = ui.state.proposal;
      const decisions = {};
      for (const d of p.details || []) decisions[d.id] = detailChoice(p, d);
      submit(`${project}/propose`, { detail_decisions: decisions });
      ui.detailChoices = {};
      break;
    }
    case "upscale-preview": submit(`${project}/upscale/preview`, { factor: ui.upscaleFactor }); break;
    case "resize-analyze": submit(`${project}/resize/analyze`, { process: processPayload(), bambu_printer: (ui.resizePrinter ?? ui.state.resize?.analysis?.bambu_printer) || null }); break;
    case "resize-preview": submit(`${project}/resize/preview`, { size_mm: Number($("#resize-size").value) }); break;
    case "resize-search": submit(`${project}/resize/search`, {}); break;
    case "resize-retry": {
      const ap = ui.resizeApproved[el.dataset.preview] || {};
      submit(`${project}/resize/retry`, { preview_id: el.dataset.preview, approve_size: Boolean(ap.size), approve_changes: Boolean(ap.changes) });
      break;
    }
    case "upscale-retry":
      submit(`${project}/upscale/retry`, { factor: Number(el.dataset.factor), preview_id: el.dataset.preview, approve_changes: Boolean(ui.upscaleApproved[el.dataset.preview]) });
      break;
    case "start-review": submit(`${project}/review`, { candidate: el.dataset.candidate }); break;
    case "select-sheet": ui.sheet = el.dataset.sheet; ui.pending = null; ui.marker = null; if (ui.view !== "front") ui.view = "sheet"; render(); break;
    case "view": ui.view = el.dataset.view; render(); break;
    case "tool":
      ui.tool = el.dataset.tool; ui.pending = null;
      if (ui.tool !== "inspect" && ui.view === "front") ui.view = "sheet";  // edits act on the selected layer: show it
      render(); break;
    case "cancel-pending": ui.pending = null; render(); break;
    case "apply-pending": if (ui.pending) editOp(ui.pending); break;
    case "propose-op": ui.pending = JSON.parse(el.dataset.op); if (ui.pending.sheet) ui.sheet = ui.pending.sheet; ui.view = "sheet"; setTab("review"); render(); break;
    case "locate":
      ui.sheet = el.dataset.sheet; ui.marker = [Number(el.dataset.x), Number(el.dataset.y)]; ui.pending = null; ui.view = "sheet"; ui.tool = "inspect";
      setTab("review"); render(); $("#canvas-wrap")?.scrollIntoView({ behavior: "smooth", block: "center" }); break;
    case "move": editOp({ op: "move", sheet, to: Number(el.dataset.to) }); break;
    case "recolor": editOp({ op: "recolor", sheet, color: $("#p-color").value.toUpperCase() }); break;
    case "thickness": editOp({ op: "thickness", sheet, mm: Number($("#p-thick").value) }); break;
    case "rim": {
      const on = $("#p-rim").checked;
      const target = $("#p-rim-all").checked ? "*" : sheet;  // "*" = every layer, in one edit
      editOp(on ? { op: "rim", sheet: target, enabled: true, width_mm: Number($("#p-rim-w").value), total_height_mm: Number($("#p-rim-h").value) } : { op: "rim", sheet: target, enabled: false });
      break;
    }
    case "merge": editOp({ op: "merge", sheet, other: $("#p-merge").value }); break;
    case "undo": submit(`${project}/undo`, {}); break;
    case "revert": submit(`${project}/undo`, { keep: Number(el.dataset.keep) }); break;
    case "verify": submit(`${project}/verify`, {}); break;
    case "reload": await loadState(); render(); break;
    case "raw-op":
      try { ui.pending = JSON.parse($("#raw-op").value); render(); } catch (err) { showError({ message: `Not valid JSON: ${err.message}` }, "Edit request"); }
      break;
    case "run-printability": submit(`${project}/printability`, { process: processPayload() }); break;
    case "build-kit":
      submit(`${project}/kits`, { process: processPayload(), bambu_printer: ui.bambu ? (ui.bambuPrinter || ui.info.bambu.default_printer) : null });
      break;
    case "verify-kit":
      try { ui.kitChecks[el.dataset.kit] = await api("POST", `${project}/kits/${encodeURIComponent(el.dataset.kit)}/verify`, {}); render(); } catch (err) { showError(err); }
      break;
    case "delete-project-ask": ui.confirmProject = el.dataset.project; ui.projectNotice = null; render(); break;
    case "delete-project-cancel": ui.confirmProject = null; render(); break;
    case "delete-project-confirmed": await deleteProject(el.dataset.project); break;
    case "dismiss-project-notice": ui.projectNotice = null; render(); break;
    case "delete-kit": ui.confirmDelete = el.dataset.kit; ui.kitNotice = null; render(); break;
    case "delete-kit-cancel": ui.confirmDelete = null; render(); break;
    case "delete-kit-confirmed": await deleteKit(el.dataset.kit); break;
    case "open-kit":
      try {
        const opened = await api("POST", `${project}/kits/${encodeURIComponent(el.dataset.kit)}/open`, {});
        ui.kitNotice = { kind: "ok", title: "Kit folder opened", text: opened.opened };
      } catch (err) {
        ui.kitNotice = { kind: "bad", title: "Could not open the kit folder", text: err.message };
      }
      render();
      break;
    case "dismiss-kit-notice": ui.kitNotice = null; render(); break;
    case "toggle-kit-build": ui.kitBuildOpen = !ui.kitBuildOpen; render(); break;
    default: break;
  }
}

function onChange(event) {
  const el = event.target;
  if (el.id === "project-select") {
    location.hash = el.value ? `#/${encodeURIComponent(el.value)}` : "#/";
  } else if (el.id === "p-rim-all") {
    ui.rimAll = el.checked;
  } else if (el.id === "resize-printer") {
    ui.resizePrinter = el.value;
  } else if (el.id === "resize-size") {
    ui.resizeSize = Number(el.value);
  } else if (el.dataset.resizeApprove) {
    const ap = ui.resizeApproved[el.dataset.preview] || (ui.resizeApproved[el.dataset.preview] = {});
    ap[el.dataset.resizeApprove] = el.checked;
    render();
  } else if (el.name === "upscale-factor") {
    ui.upscaleFactor = Number(el.value);
    render();
  } else if (el.dataset.upscaleApprove) {
    ui.upscaleApproved[el.dataset.upscaleApprove] = el.checked;
    render();
  } else if (el.dataset.detail && el.type === "radio") {
    const p = ui.state.proposal;
    const d = p.details.find((x) => x.id === el.dataset.detail);
    const current = detailChoice(p, d);
    ui.detailChoices[d.id] = { action: el.value, ...(el.value === "adjust" ? { color: (current.color || d.processed_hex).toUpperCase() } : {}) };
    render();
  } else if (el.dataset.detailColor) {
    ui.detailChoices[el.dataset.detailColor] = { action: "adjust", color: el.value.toUpperCase() };
    render();
  } else if (el.id === "kit-bambu") {
    ui.bambu = el.checked;
    render();
  } else if (el.id === "kit-bambu-printer") {
    ui.bambuPrinter = el.value;
  } else if (el.dataset.action === "preset") {
    ui.process = { ...ui.info.process_presets[Number(el.value)] };
    render();
  } else if (el.dataset.process) {
    ui.process[el.dataset.process] = el.value;
    if (!/user-entered/.test(ui.process.source)) {
      ui.process.name = `${ui.process.name.replace(/_custom$/, "")}_custom`;
      ui.process.source = "user-entered in the LayerForge app (not a measured profile unless you measured it)";
    }
    render();
  } else if (el.id === "bar-width") {
    ui.barWidth = Math.max(0.1, Number(el.value) || 1.2);
  } else if (el.id === "precision") {
    ui.precision = el.value;
  }
}

// ------------------------------------------------------------------------------- start
async function main() {
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  // Remember sections the user opens or closes (only clicks: renders also toggle <details>).
  document.addEventListener("click", (e) => {
    const details = e.target.closest("summary")?.parentElement;
    if (details?.dataset.section) ui.sections[details.dataset.section] = !details.open;
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => { if (ui.tab === "review") drawOverlay(); });
  try {
    ui.info = await api("GET", "/api/info");
    ui.process = { ...ui.info.process_presets[0] };
    await loadProjects();
  } catch (err) {
    showError(err, "Cannot reach the LayerForge server");
    return;
  }
  await route();
}

main();
