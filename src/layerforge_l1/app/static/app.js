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
  pending: null,       // accept_artwork awaiting "Apply" (the only edit proposed from Review Layers)
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
  cleanupChoice: null, // speckle cleanup on/off chosen but not yet re-run (null: as planned)
  cleanupView: "after", // Color cleanup image: "after" | "before"
  upscaleFactor: 2,    // Upscale & Retry: processing-resolution factor shown
  upscaleApproved: {}, // preview id -> the user approved its visible changes
  resizePrinter: null, // Bambu printer whose usable area limits the sheet size ("" = the process bed; null = as last checked)
  resizeSize: null,    // sheet size (mm) entered for the resize preview
  resizeApproved: {},  // resize preview id -> {size, changes} approvals
  confirmProject: null, // project name whose inline delete confirmation is shown
  projectNotice: null,  // result of the last project deletion (shown on the project list)
  planLater: false,     // import: stop after intake so artwork colors can be edited before planning
  art: freshArt(),      // artwork recolor panel (flat-color target, not a sheet)
  fit: freshFit(),      // Fit artwork to canvas card (inside the Artwork editor)
  insp: freshInsp(),    // issue inspector: selection, filters, view and zoom
  inspA: { zoom: null, pan: [0, 0], panning: null },  // issue inspector: processed-image view (follows insp)
  inspPad: null,        // the processed image padded to the sheet extent ({src, url}) for the inspector
  issues: null,         // /issues of the current state (null: to be loaded)
  issuesLoading: false,
  issuesDirty: true,    // the shown issues may be older than the state: reload (keeping them shown meanwhile)
  artSpace: false,      // Space held: a left drag on a zoomable image pans
  editView: { zoom: null, pan: [0, 0], panning: null },  // Review Layers canvas zoom (viewport only)
};

// Artwork editor: selected regions, a chosen color, the pending Line / Curve / Close gap (with the
// server's preview of its pixels) and the last result. Every completed edit goes straight into the
// unsaved working session on the server; Save Changes saves them all at once.
function freshArt() {
  return { mode: "select", box: null, drag: null, regions: [], selection: null, preview: null, color: null, source: null,
           sampled: null, zoom: null, pan: [0, 0], panning: null,
           width: 1.2, stroke: null, gap: null, draw: null, handle: null, prevMode: null,
           last: null, confirmDiscard: false, recovered: false, focus: null };
}

const unsavedArt = () => ui.state?.artwork?.unsaved?.count || 0;

const TABS = [
  ["proposal", "Import & proposal"],
  ["review", "Review layers"],
  ["printability", "Printability"],
  ["kit", "Print kit"],
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
  ui.issuesDirty = true;  // diagnostics are read again for the new state (never edited locally)
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
  if (job.status !== "done") { ui.inspPendingEdit = null; return; }
  if (job.kind === "propose" || job.kind === "upscale_preview") ui.tab = "proposal";
  if (job.kind === "upscale_retry") { ui.tab = "proposal"; ui.upscaleApproved = {}; }
  if (job.kind === "resize_analysis") { setTab("proposal"); ui.resizeSize = null; }
  if (job.kind === "resize_preview" || job.kind === "resize_search") setTab("proposal");
  if (job.kind === "resize_retry") { setTab("proposal"); ui.resizeApproved = {}; ui.resizeSize = null; }
  if (job.kind === "start_review") {
    setTab("review");
    const pending = ui.inspPendingEdit;  // Show on layer from a candidate's issue: show it in the review
    ui.inspPendingEdit = null;
    if (pending && ui.state.draft) setTimeout(() => inspEditLayer(pending), 0);
  }
  if (job.kind === "artwork_replan") { setTab("proposal"); ui.art = freshArt(); ui.fit = freshFit(); }
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
    artwork_replan: "Planning sheets from the edited artwork (L1 proposal, validation, CAD check; previous attempt kept)",
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

function leaveArtworkOk() {
  const n = unsavedArt();
  return !n || confirm(`${n} unsaved artwork change${n === 1 ? "" : "s"} in ${ui.name}. They are kept with the project and shown again when you come back. Leave this project anyway?`);
}

async function route() {
  const [, name, tab] = location.hash.replace(/^#/, "").split("/").map(decodeURIComponent);
  if ((name || null) !== ui.name && ui.name && !leaveArtworkOk()) {
    history.replaceState(null, "", `#/${encodeURIComponent(ui.name)}/${ui.tab}`);  // stay
    renderProjectSelect();
    return;
  }
  ui.error = null;
  if (name && name !== ui.name) {
    ui.name = name;
    ui.state = null;
    ui.pending = null;
    ui.cleanupChoice = null;
    ui.marker = null;
    ui.sheet = null;
    ui.job = null;
    ui.art = freshArt();
    ui.fit = freshFit();
    ui.insp = freshInsp();
    ui.inspA = { zoom: null, pan: [0, 0], panning: null };
    ui.issues = null;
    ui.editView = { zoom: null, pan: [0, 0], panning: null };
    try {
      await loadState();
      ui.art.recovered = unsavedArt() > 0;  // unsaved changes from an earlier visit, kept on disk
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
  const focusKey = artFocusKey();
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
    <div class="project-head">
      <div><h1>${esc(ui.state.name)}</h1>
        <p class="muted small">${esc(ui.state.record.source.original_name)} · ${esc(ui.state.resize?.sheet_mm ?? ui.state.record.proposal_settings.outer_mm)} mm sheet · scale ${esc(ui.state.upscale?.scale ?? ui.state.record.proposal_settings.scale)} ${infoTip(`Source image sha256 ${ui.state.record.source.sha256}. The imported image is never changed.`)}</p>
      </div>
      ${ui.confirmProject === ui.state.name ? "" : `<button class="small ghost danger" data-action="delete-project-ask" data-project="${esc(ui.state.name)}" data-testid="project-delete" ${busy() ? 'disabled title="Wait for the running job to finish"' : ""}>Delete project…</button>`}
    </div>
    ${ui.confirmProject === ui.state.name ? projectDeleteConfirm(ui.state.name) : ""}
    ${workflowNav()}
    ${statusStrip()}
    <div id="job-slot">${jobBanner()}</div>
    ${errorBanner()}
    <section id="tab-body">${tabBody()}</section>`;
  if (ui.tab === "review" && ui.state.draft) {
    bindCanvas();
    drawOverlay();
  }
  bindArtwork();
  bindFit();
  bindInspector();
  restoreArtFocus(focusKey);
}

// The workflow as one stepper: Import -> Edit artwork -> Plan -> Review layers -> (Check printability)
// -> Export.  Each step opens the tab where it happens (tab ids unchanged) and shows its state.
const STEP_MARK = { done: "✓", warn: "!", bad: "✕" };
function workflowSteps() {
  const s = ui.state;
  const p = s.proposal;
  const d = s.draft;
  const a = s.artwork;
  const kit = latestKit();
  const edits = a?.edits?.length || 0;
  const selected = p?.candidates?.find((c) => c.name === p.selected);
  const plan = !p ? ["todo", busy() ? "Running…" : "Not run"]
    : p.status === "PROPOSAL_READY_FOR_REVIEW" ? ["done", selected ? `${selected.sheet_count} sheets` : "Ready"]
    : p.status === "ARTWORK_READY_NOT_PLANNED" ? ["todo", "Not planned yet"] : ["warn", "Needs decision"];
  const art = unsavedArt() ? ["warn", `${unsavedArt()} unsaved`]
    : a?.differs_from_planned || (edits && !a.planned) ? ["warn", `${edits} change${edits === 1 ? "" : "s"} not planned`]
    : edits ? ["todo", `${edits} change${edits === 1 ? "" : "s"}`] : ["todo", "Optional"];
  const review = !d ? ["todo", "Not started"] : d.status.printable ? ["done", `${d.sheets.length} layers`] : ["bad", "Invalid draft"];
  const est = s.printability;
  const check = !est ? ["todo", "Optional"] : est.stale ? ["todo", "Out of date"]
    : est.status === "NO_RISKS_FOUND" ? ["done", "No risks"] : est.status === "RISKS_REPORTED" ? ["warn", "Risks"] : ["bad", "Unsupported"];
  const exp = !kit ? ["todo", "No kit yet"] : kit.stale ? ["warn", "Kit out of date"] : kit.status.cad === "CAD_VALID" ? ["done", "Kit ready"] : ["bad", "CAD problem"];
  return [
    { key: "import", label: "Import", state: ["done", "Image"], tab: null },
    { key: "artwork", label: "Edit artwork", state: art, tab: d ? "review" : "proposal", focus: "artwork" },
    { key: "plan", label: "Plan", state: plan, tab: "proposal" },
    { key: "review", label: "Review layers", state: review, tab: "review" },
    { key: "printability", label: "Check printability", state: check, tab: "printability", minor: true },
    { key: "kit", label: "Export", state: exp, tab: "kit" },
  ];
}

function workflowNav() {
  const steps = workflowSteps();
  const current = { proposal: "plan", review: "review", printability: "printability", kit: "kit" }[ui.tab];
  return `<nav class="workflow" role="tablist" aria-label="Workflow">${steps.map((st, i) => {
    const [state, sub] = st.state;
    const mark = STEP_MARK[state] || String(i + 1);
    const body = `<span class="wf-dot" aria-hidden="true">${mark}</span><span class="wf-text"><span class="wf-label">${esc(st.label)}</span><span class="wf-sub">${esc(sub)}</span></span>`;
    const cls = `wf-step state-${state}${st.minor ? " minor" : ""}${st.key === current ? " current" : ""}`;
    const step = st.tab
      ? `<button class="${cls}" role="tab" aria-selected="${st.key === current}" data-action="tab" data-tab="${st.tab}"${st.focus ? ` data-focus="${st.focus}"` : ""} data-step="${st.key}" ${tabEnabled(st.tab) ? "" : "disabled"}>${body}</button>`
      : `<div class="${cls}" data-step="${st.key}">${body}</div>`;
    return (i ? '<span class="wf-sep" aria-hidden="true">›</span>' : "") + step;
  }).join("")}</nav>`;
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
  if (unsavedArt()) {
    geo = ["warn", "Unsaved artwork changes", `${unsavedArt()} artwork change(s) are not saved yet. Save or discard them in the Artwork editor; export is blocked until then.`];
  } else if (s.artwork?.differs_from_planned) {
    geo = ["warn", "Artwork edited — sheets not re-planned", `The sheets are validated against the previous artwork only (${s.artwork.edits.length} artwork edit(s) since). Plan from the edited artwork or undo the artwork edits; export is blocked until then.`];
  }
  let fdm = ["neutral", "Not estimated", "Run the printability estimate or build a kit."];
  const est = kit && !kit.stale ? { status: kit.printability.status, counts: kit.printability.counts, from: `kit ${kit.id}` }
    : s.printability && !s.printability.stale ? { status: s.printability.status, counts: s.printability.counts, from: "draft estimate" } : null;
  if (est) {
    const cls = est.status === "NO_RISKS_FOUND" ? "ok" : est.status === "RISKS_REPORTED" ? "warn" : "bad";
    fdm = [cls, fdmLabel(est.status), `${est.counts.UNSUPPORTED} unsupported · ${est.counts.RISK} risk (${est.from})`];
  } else if (s.printability || kit) {
    fdm = ["neutral", "Estimate out of date", "The draft changed since the last estimate."];
  }
  return `<div class="status-strip" data-testid="status-strip">
    ${statusCard("Geometry / CAD", geo, "status-geometry")}
    ${statusCard("FDM printability (estimate)", fdm, "status-fdm")}
    <div class="status-card neutral physical" data-testid="status-physical">
      <div class="label">Physical print</div>
      <div class="value">${badge("outline", "NOT VERIFIED")}</div>
      <div class="note">Only your test print can confirm a result. ${infoTip(s.physical.note + " Test print L-G10 is pending.")}</div>
    </div>
  </div>`;
}

function statusCard(label, [cls, value, note], testid) {
  return `<div class="status-card ${cls}" data-testid="${testid}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="note" title="${esc(note)}">${esc(note)}</div></div>`;
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
      <p class="muted">Import a flat-color PNG or JPEG. ${infoTip("LayerForge prepares the flat-color artwork, plans physical layer sheets automatically, then you review, edit and export a print kit.")}</p>
      <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Choose an image">
        ${ui.upload ? `<strong>${esc(ui.upload.name)}</strong> <span class="muted">(${(ui.upload.size / 1024).toFixed(0)} KB)</span><img id="upload-preview" alt="selected image">` : "<strong>Drop an image here</strong><br><span class=\"muted\">or click to choose a file</span>"}
        <input type="file" id="file-input" accept=".png,.jpg,.jpeg,.jfif,.jpe,image/png,image/jpeg" hidden>
      </div>
      <label class="field"><span>Project name</span><input id="f-name" placeholder="from file name"></label>
      <label class="check small"><input type="checkbox" id="f-plan-later" data-testid="plan-later" ${ui.planLater ? "checked" : ""}> Edit artwork colors before planning ${infoTip("Prepares the flat-color artwork only. You plan the sheets when you choose.")}</label>
      <details class="section subtle" data-section="import-settings" ${openAttr("import-settings")}>
        <summary><h4 style="margin:0">Settings</h4><span class="small muted">sheet size, thickness, grid scale, colors</span></summary>
        <div class="section-body">
      <div class="form-grid">
        <label class="field"><span>Grid scale</span><select id="f-scale">${scales.map((s) => `<option value="${s}" ${s === 2 ? "selected" : ""}>${s} · ${(0.390625 / s).toFixed(3)} mm/px${s === 1 ? " · fast" : s === 4 ? " · slow" : ""}</option>`).join("")}</select></label>
        <label class="field"><span>Sheet size (mm)</span><input id="f-outer" type="number" value="225" min="50" step="1"></label>
        <label class="field"><span>Sheet thickness (mm)</span><input id="f-thick" type="number" value="1.6" min="0.2" step="0.2"></label>
        <label class="field"><span>Max colors</span><input id="f-colors" type="number" value="12" min="2" max="32"></label>
      </div>
      <p class="small muted">The artwork fills the sheet minus a 12.5 mm shared border. Scale 4 can take many minutes and much memory.</p>
        </div>
      </details>
      <div class="row"><button class="primary" data-action="create" ${ui.upload && !busy() ? "" : "disabled"}>${ui.planLater ? "Import and prepare artwork" : "Import and propose layers"}</button></div>
    </div>
    <div class="panel stack">
      <h2>Projects</h2>
      ${projectNotice()}
      ${ui.projects.length ? `<table><thead><tr><th>Name</th><th>Stage</th><th>Created (UTC)</th><th></th></tr></thead><tbody>${ui.projects.map((p) => `
        <tr data-testid="project-row" data-project="${esc(p.name)}"><td><a href="#/${encodeURIComponent(p.name)}">${esc(p.name)}</a></td><td>${esc(p.stage)}</td><td class="small muted">${esc(p.created_utc)}</td>
          <td>${ui.confirmProject === p.name ? "" : `<button class="small ghost danger" data-action="delete-project-ask" data-project="${esc(p.name)}" data-testid="project-delete">Delete…</button>`}</td></tr>
        ${ui.confirmProject === p.name ? `<tr><td colspan="4">${projectDeleteConfirm(p.name)}</td></tr>` : ""}`).join("")}</tbody></table>`
        : '<p class="muted">No projects yet.</p>'}
      <p class="small muted">Saved automatically. ${infoTip("Projects are saved in the workspace folder after every step and reopen after a restart.")}</p>
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
  if (ui.planLater) params.set("plan", "0");
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
      <p class="muted">${busy() ? "The L1 proposal is running: color intake, the approved planner for each candidate, validation, and an STL export of the selected candidate that is reopened and checked. STEP files are made and verified with the print kit." : "No proposal yet."}</p>
      ${busy() ? "" : `<button class="primary" data-action="propose">Run the proposal</button>`}
      <figure style="max-width:280px"><img class="img" src="${esc(ui.state.source_url)}" alt="source image"><figcaption>Source image as uploaded</figcaption></figure>
    </div>`;
  }
  const ready = p.status === "PROPOSAL_READY_FOR_REVIEW";
  const notPlanned = p.status === "ARTWORK_READY_NOT_PLANNED";
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
        reviewing ? "" : `<button class="small" data-action="start-review" data-candidate="${esc(c.name)}" ${busy() ? "disabled" : ""}>Review this candidate</button>`}</td>
    </tr>`).join("");
  const img = p.images || {};
  const obstruction = p.candidates.filter((c) => c.obstruction_url);
  const sel = p.candidates.find((c) => c.name === p.selected);
  const blockers = (p.flags || []).filter((f) => f.severity === "BLOCKER");
  const notes = (p.flags || []).filter((f) => f.severity !== "BLOCKER");
  const warnings = notes.filter((f) => f.severity === "WARN").length;
  const cadLine = p.cad ? `<p class="small muted" style="margin:0">CAD check of the selected candidate: reopened STL ${p.cad.revalidation_pass ? "PASS" : "FAIL"}, stack mismatch ${fmt(p.cad.stack_mismatch_px)} px, ${p.cad.step_verification === "PENDING_PRINT_KIT" ? "STEP verified with the print kit" : `STEP single solids ${p.cad.all_step_valid_single_solid ? "yes" : "no"}`}, watertight STL ${p.cad.all_stl_watertight_single_component ? "yes" : "no"}.</p>` : "";
  const reviewAction = !sel ? "" : reviewing
    ? `<a class="button" href="#/${encodeURIComponent(ui.name)}/review" data-action="tab" data-tab="review">Open the editor →</a><span class="small muted">Reviewing <strong>${esc(reviewing)}</strong></span>`
    : `<button class="primary large" data-action="start-review" data-candidate="${esc(sel.name)}" ${busy() ? "disabled" : ""}>Review this candidate</button>`;
  const summary = sel ? `<div class="plan-summary">
      ${img.assembled_front ? `<img class="img plan-thumb" src="${esc(img.assembled_front)}" alt="assembled front view of the selected plan">` : ""}
      <div class="stack" style="gap:6px">
        <div class="plan-figure"><strong>${fmt(sel.sheet_count)} sheets</strong> <span class="muted">· ${(p.palette || []).length} colors</span></div>
        <div class="row" title="Colors front to back">${(sel.front_to_back_colors || []).map((h) => swatch(h, true)).join("")}<span class="small muted">front → back</span></div>
        <div class="small muted">Candidate <strong>${esc(sel.name)}</strong> · ${fmt(sel.source_regions_split_across_sheets)} split region(s) · ${fmt(sel.reconstruction_mismatched_pixels)} px mismatch</div>
        ${p.cad && p.cad.step_verification === "PENDING_PRINT_KIT" ? `<div class="small muted" data-testid="step-pending">STEP check: done when you build the print kit.</div>` : ""}
        <div class="row">${reviewAction}</div>
      </div></div>`
    : `<div class="row"><h4 style="margin:0">Palette</h4>${(p.palette || []).map((h) => swatch(h, true)).join("")}</div>`;
  return `<div class="panel stack">
      <div class="spread">
        <h2>Plan ${badge(ready ? "ok" : notPlanned ? "neutral" : "warn", ready ? "Ready for review" : notPlanned ? "Artwork prepared · not planned yet" : "Needs your decision")}</h2>
        <span class="muted small">Automatic proposal · ${fmt(p.seconds, 1)} s${p.artwork_edits ? ` · planned from artwork you edited (${p.artwork_edits.region_recolors} artwork edit${p.artwork_edits.region_recolors === 1 ? "" : "s"})` : ""} ${infoTip("No manual layer plan is used: the sheets are proposed automatically, validated, exported to STL, reopened and checked. STEP files are exported and verified when you build the print kit.")}</span>
      </div>
      ${notPlanned ? `<div class="banner info">The flat-color artwork is ready; no sheets are planned yet. Edit the artwork in the <strong>Artwork editor</strong> below if needed, then choose <strong>Plan sheets from this artwork</strong>.</div>` : ""}
      ${p.artwork_check ? artworkCheck(p.artwork_check) : ""}
      ${ready || notPlanned ? "" : `<div class="banner warn">No candidate passed every check. Nothing was edited automatically. ${obstruction.length ? "The obstruction maps (Previews) show where the planner cannot support the artwork." : "See the notes below."}${ui.state.upscale?.available ? " You can also try <strong>Upscale &amp; Retry</strong> below." : ""}</div>`}
      ${blockers.length ? `<ul class="banner error small blockers" data-testid="proposal-blockers">${blockers.map((f) => `<li><strong>${esc(f.code)}</strong> — ${esc(f.message)}</li>`).join("")}</ul>` : ""}
      ${ui.state.upscale && ui.state.upscale.scale !== ui.state.upscale.import_scale ? `<p class="small muted" data-testid="processing-scale">Processing resolution: scale ${ui.state.upscale.scale} (imported at scale ${ui.state.upscale.import_scale}; raised by Upscale &amp; Retry). Physical size unchanged.</p>` : ""}
      ${summary}
      ${rows ? `<details class="section subtle" data-section="candidates" ${openAttr("candidates", !ready && !notPlanned)}>
        <summary><h4 style="margin:0">Compare candidates</h4><span class="small muted">${p.candidates.length} candidate${p.candidates.length === 1 ? "" : "s"} · ${p.candidates.filter((c) => c.status === "VALID").length} valid</span></summary>
        <div class="section-body"><div class="table-wrap"><table>
        <thead><tr><th>Candidate</th><th>Status</th><th>Sheets</th><th>Colors front → back</th><th>Split regions (seams)</th><th>Mismatch px</th><th>Time s</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div></div></details>` : ""}
      ${notes.length || cadLine ? `<details class="section subtle" data-section="flags" ${openAttr("flags", !ready && !notPlanned)}>
        <summary><h4 style="margin:0">Notes</h4><span class="small muted">${notes.length} note${notes.length === 1 ? "" : "s"}${warnings ? ` · ${warnings} to check` : ""}</span></summary>
        <div class="section-body stack" style="gap:6px"><ul class="small" style="margin:0">${notes.map((f) => `<li>${badge(f.severity === "WARN" ? "warn" : "neutral", f.severity)} <strong>${esc(f.code)}</strong> — ${esc(f.message)}</li>`).join("")}</ul>${cadLine}</div></details>` : ""}
    </div>
    ${ui.state.draft ? "" : issuesPanel()}
    ${ui.state.draft ? `<p class="small muted panel tight">The artwork is edited in <a href="#/${encodeURIComponent(ui.name)}/review" data-action="tab" data-tab="review" data-focus="artwork">Review layers</a> while a review exists.</p>` : artworkPanel()}
    ${resizePanel()}
    ${upscalePanel()}
    ${cleanupPanel(p, reviewing)}
    ${detailsPanel(p, reviewing)}
    ${attemptsPanel()}
    <details class="panel section" data-section="proposal-previews" ${openAttr("proposal-previews", !ready && !notPlanned)}>
      <summary><h3>Previews</h3><span class="small muted">source · flat-color target · assembled · exploded${obstruction.length ? " · obstruction maps" : ""} ${infoTip("Drawn from the reopened STL of the selected candidate. Click an image to open it full size.")}</span></summary>
      <div class="section-body stack">
      <div class="gallery compact">
        <figure><img class="img" src="${esc(ui.state.source_url)}" alt="source"><figcaption>Source image</figcaption></figure>
        ${img.target ? `<figure><img class="img" src="${esc(img.target)}" alt="target"><figcaption>Flat-color target</figcaption></figure>` : ""}
        ${img.assembled_front ? `<figure><img class="img" src="${esc(img.assembled_front)}" alt="assembled"><figcaption>Assembled front view</figcaption></figure>` : ""}
        ${img.exploded ? `<figure><img class="img" src="${esc(img.exploded)}" alt="exploded"><figcaption>Exploded stack</figcaption></figure>` : ""}
        ${obstruction.map((c) => `<figure><img class="img" src="${esc(c.obstruction_url)}" alt="obstruction"><figcaption>${esc(c.name)}: cells the planner cannot support</figcaption></figure>`).join("")}
      </div>
      ${img.source_comparison ? `<figure style="margin:0"><img class="img" style="max-width:720px" src="${esc(img.source_comparison)}" alt="comparison"><figcaption>Target | assembled | mismatches in red</figcaption></figure>` : ""}
      </div>
    </details>`;
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
  const failed = ui.state.proposal?.status === "NEEDS_USER_DECISION";
  return `<details class="panel section" data-section="resize" data-testid="resize-panel" ${openAttr("resize", Boolean(a || last || failed))}>
    <summary><h3>Physical size ${r.sheet_mm !== r.import_sheet_mm ? badge("neutral", `resized from ${fmt(r.import_sheet_mm, 1)} mm`) : ""}</h3><span class="small muted">${fmt(r.sheet_mm, 1)} mm sheet · check whether a larger sheet prints better</span></summary>
    <div class="section-body stack">
    <p class="small muted" style="margin:0">A larger sheet widens every opening and strip by the same ratio. Nothing is resized without your approval. ${infoTip("Every printability threshold is in millimetres (planner structural width, nozzle, minimum opening and material width), so some findings clear by size alone; others (topology, unmeasured necks) cannot. The image is never edited.")}</p>
    ${last ? resizeCheck(last) : ""}
    ${r.available ? `<details class="section" data-section="resize-process" ${openAttr("resize-process")}><summary>Printer and process for the check: <strong>${esc(ui.process.name)}</strong></summary>${processForm("rs")}</details>
    <div class="row">${printers}<button class="${a ? "" : "primary"}" data-action="resize-analyze" data-testid="resize-analyze" ${busy() ? "disabled" : ""}>Check printability at larger sizes</button></div>`
      : `<p class="small muted">${esc(r.reason || "")}</p>`}
    ${a ? resizeAnalysis(a) : ""}
    ${a && r.preview ? resizePreview(r.preview, a) : ""}
    </div>
  </details>`;
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
      ${v.artwork_edits ? `<div class="banner info small" data-testid="resize-keeps-artwork">Your ${v.artwork_edits.region_recolors} artwork edit${v.artwork_edits.region_recolors === 1 ? "" : "s"}${v.artwork_edits.pending ? " (not planned yet)" : ""} ${v.artwork_edits.region_recolors === 1 ? "is" : "are"} kept: the edited artwork is planned at the new size pixel for pixel, and its edit history moves with the new proposal. ${esc(v.artwork_edits.note)}</div>`
        : `<p class="small muted">Same pixels and colors; the intake filters work in millimetres, so a larger sheet can keep details a smaller one merged. ${(v.details || []).length} intake detail(s) at this size (removed pieces such as eye highlights are kept by default).</p>`}
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
  if (u.blocked_by_artwork) {
    return `<section class="panel stack" data-testid="upscale-panel"><h3>Upscale &amp; Retry <span class="small muted">(processing resolution, not physical size)</span></h3>
      <div class="banner info small" data-testid="upscale-blocked">${esc(u.blocked_by_artwork)}</div></section>`;
  }
  const option = u.options.find((o) => o.factor === ui.upscaleFactor) || u.options[0];
  const preview = u.previews?.[String(option.factor)];
  const radios = u.options.map((o) => `<label class="check" title="${esc(o.reason || "")}">
      <input type="radio" name="upscale-factor" value="${o.factor}" ${o.factor === option.factor ? "checked" : ""} ${busy() ? "disabled" : ""}>
      <strong>${o.factor}×</strong> <span class="small muted">scale ${o.scale} · ${fmt(o.mm_per_pixel, 4)} mm/px · est. peak ${fmt(o.estimated_peak_gb, 1)} GB, ${fmt(o.available_gb, 1)} GB free now${o.estimated_minutes != null ? ` · roughly ${fmt(o.estimated_minutes, 0)} min or more` : ""}</span>
      ${o.fits ? "" : badge("bad", "not enough memory", o.reason)}</label>`).join("");
  return `<section class="panel stack" data-testid="upscale-panel">
    <h3>Upscale &amp; Retry <span class="small muted">(processing resolution, not physical size)</span></h3>
    <p class="small">Plan again with 2× or 4× more pixels per millimetre. Physical size and rules stay the same. ${infoTip("Only the processing resolution changes: the sheet size, artwork aperture, shared perimeter, artwork position and aspect stay as they are, and every physical rule and minimum width is unchanged. The image is resampled by the normal intake (no AI upscaling, no invented detail). A finer grid can represent thin details more faithfully, but it does not necessarily resolve physical or topological constraints (openings narrower than the structural width, unsupportable islands).")}</p>
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
      <td class="small">${a.sheet_mm ? `${fmt(a.sheet_mm, 1)} mm sheet · ` : ""}replaced by ${a.replaced_by.kind === "physical_resize" ? `resize to ${fmt(a.replaced_by.sheet_mm, 1)} mm` : a.replaced_by.kind === "artwork_edit" ? `planning from the edited artwork (${a.replaced_by.region_recolors} artwork edit${a.replaced_by.region_recolors === 1 ? "" : "s"})` : `${a.replaced_by.upscale_factor}× processing (scale ${a.replaced_by.scale})`}: ${esc(a.replaced_by.status)}; ${a.replaced_by.approved_visible_changes} visible changes approved</td>
      <td class="small"><a href="${esc(a.images.target)}" target="_blank" rel="noopener">target</a>${a.images.obstruction.map((u) => ` · <a href="${esc(u)}" target="_blank" rel="noopener">obstruction map</a>`).join("")} · ${a.images.comparison ? `<a href="${esc(a.images.comparison)}" target="_blank" rel="noopener">preview comparison</a>` : ""}</td></tr>`).join("");
  return `<details class="panel section" data-section="attempts" data-testid="attempts" ${openAttr("attempts")}>
    <summary><h3>Previous attempts ${badge("neutral", String(attempts.length))}</h3><span class="small muted">archived proposals with their diagnostics and settings</span></summary>
    <div class="section-body"><div class="table-wrap"><table><thead><tr><th>Attempt</th><th>Result</th><th>Candidates</th><th>Retry</th><th>Evidence</th></tr></thead><tbody>${rows}</tbody></table></div></div>
  </details>`;
}

// Details intake would recolor or erase: original | default intake | as planned, with a choice each.
// Intake speckle cleanup: patches with no drawn edge in the source were merged into the color
// around them. Shown only when it changed something or is turned off; one switch, before/after.
function cleanupPanel(p, reviewing) {
  const c = p.cleanup;
  if (!c || (c.enabled && !c.pixels)) return "";
  const on = ui.cleanupChoice ?? c.enabled;
  const changed = on !== c.enabled;
  const before = p.images?.before_cleanup;
  const view = before && ui.cleanupView === "before" ? "before" : "after";
  const blocked = reviewing || p.intake_rerun_blocked;
  const status = !c.enabled ? "off" : `${fmt(c.regions_before)} → ${fmt(c.regions_after)} color regions`;
  return `<details class="panel section" data-section="cleanup" data-testid="cleanup-panel" ${openAttr("cleanup", changed)}>
    <summary><h3>Color cleanup</h3><span class="small muted">${status}</span></summary>
    <div class="section-body stack">
      <p class="small muted" style="margin:0">Patches with no drawn edge in the source are merged into the color around them.</p>
      ${before ? `<div class="segmented" role="tablist" aria-label="Cleanup view" data-testid="cleanup-view">${[["after", "After"], ["before", "Before"]].map(([v, l]) =>
        `<button class="small ${view === v ? "active" : ""}" data-action="cleanup-view" data-view="${v}" role="tab" aria-selected="${view === v}">${l}</button>`).join("")}</div>
      <img class="img cleanup-img" src="${esc(view === "before" ? before : p.images.target)}" alt="flat-color artwork ${view} the cleanup" data-testid="cleanup-image">` : ""}
      <div class="row">
        <label class="check"><input type="checkbox" id="cleanup-on" data-testid="cleanup-toggle" ${on ? "checked" : ""} ${blocked || busy() ? "disabled" : ""}> Merge patches without a drawn edge</label>
        ${changed && !blocked ? `<button class="primary small" data-action="apply-cleanup" data-testid="apply-cleanup" ${busy() ? "disabled" : ""}>Re-run</button>` : ""}
      </div>
      ${blocked ? `<p class="small muted" style="margin:0">${reviewing ? "A review has started; the cleanup can no longer change for this project." : esc(p.intake_rerun_blocked)}</p>` : ""}
    </div>
  </details>`;
}

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
  return `<details class="panel section" data-section="intake-details" data-testid="intake-details" ${openAttr("intake-details", !reviewing && changed)}>
    <summary><h3>Details changed by intake ${badge("warn", `${details.length}`)}</h3>
      <span class="small muted">original | default intake | as planned · nothing is discarded without your choice</span></summary>
    <div class="section-body stack">
      <p class="small muted">Removed pieces (e.g. eye whites) are kept by default, however small. Colors missing from the palette (e.g. lips, gold) keep the nearest palette color until you choose: preserving one adds its color as a new filament.${p.palette_added?.length ? ` Added by your choices: ${p.palette_added.map((h) => `${swatch(h)} ${esc(h)}`).join(" ")}.` : ""}</p>
      ${p.images?.details_overview ? `<details data-section="intake-details-overview" ${openAttr("intake-details-overview")}><summary>Where they are (numbered)</summary><img class="img" style="max-width:640px" src="${esc(p.images.details_overview)}" alt="details overview"></details>` : ""}
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Original | default | planned</th><th>Detail</th><th>Choice</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${reviewing ? '<p class="small muted">A review has started; the proposal (and these choices) can no longer be re-run for this project.</p>'
        : p.intake_rerun_blocked ? `<div class="banner info small" data-testid="intake-rerun-blocked">${esc(p.intake_rerun_blocked)}</div>`
        : `<div class="row"><button class="${changed ? "primary" : ""}" data-action="apply-details" data-testid="apply-details" ${busy() ? "disabled" : ""}>Re-run the proposal with these choices</button>
          ${changed ? '<span class="small">Choices changed: re-run to plan them.</span>' : ""}</div>`}
    </div>
  </details>`;
}

// ----------------------------------------------------------------------- artwork colors
// Region recolor of the processed flat-color artwork (the target the sheets must reproduce). An
// artwork edit, not a sheet edit: no sheet changes until the user plans again from the artwork.
// The selection is a list of regions ({at_px, box_px?}); the server resolves each to its connected
// region, toggles Shift+Click and previews/applies all of them as one edit.
const ART_SOURCES = { palette: "palette color", picker: "color picker", eyedropper: "eyedropper" };

function artworkCheck(c) {
  const n = (v) => (v == null ? "–" : String(v));
  return `<div class="subpanel stack" data-testid="artwork-check">
    <h4 style="margin:0">Planned from the edited artwork ${badge(c.after.status === "PROPOSAL_READY_FOR_REVIEW" ? "ok" : "warn", c.after.status)}</h4>
    <div class="table-wrap"><table><thead><tr><th></th><th>Palette (colors)</th><th>Sheets (measured)</th><th>Status</th></tr></thead><tbody>
      <tr><td>Before (${esc(c.before.stage)})</td><td>${n(c.before.palette_size)}</td><td>${n(c.before.sheet_count)}</td><td class="small">${esc(c.before.status)}</td></tr>
      <tr data-testid="artwork-check-after"><td>After ${c.region_recolors} artwork edit${c.region_recolors === 1 ? "" : "s"}</td><td>${n(c.after.palette_size)}</td><td>${n(c.after.sheet_count)}${c.after.selected ? ` <span class="muted small">(${esc(c.after.selected)})</span>` : ""}</td><td class="small">${esc(c.after.status)}</td></tr>
    </tbody></table></div>
    <p class="small muted" style="margin:0">${esc(c.note)}${c.archived_as ? ` The previous attempt is kept as <code>${esc(c.archived_as)}</code> (Previous attempts).` : ""}</p></div>`;
}

const swatches = (hexes) => (hexes || []).map((h) => swatch(h)).join(" ");

// Icons for the Artwork colors panel: 16 px line drawings in the current text color.
const svg = (body) => `<svg class="icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
const ICON = {
  select: svg('<path d="M5 3l14 7-6 2-3 6z"/>'),
  box: svg('<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/>'),
  pipette: svg('<path d="M4 20l1.5-4.5L14 7l3 3-8.5 8.5z"/><path d="M13 6l1-1a2.1 2.1 0 0 1 3 0l2 2a2.1 2.1 0 0 1 0 3l-1 1"/><path d="M12 5l7 7"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  fit: svg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
  undo: svg('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'),
  check: svg('<path d="M5 12l5 5 9-10"/>'),
  plan: svg('<path d="M7 4l12 8-12 8z"/>'),
  fill: svg('<path d="M5 11l6-6 8 8-6 6z"/><path d="M5 11h14"/><path d="M20 16s1.5 2 1.5 3a1.5 1.5 0 0 1-3 0c0-1 1.5-3 1.5-3z"/>'),
  line: svg('<path d="M5 19L19 5"/><circle cx="5" cy="19" r="1.5"/><circle cx="19" cy="5" r="1.5"/>'),
  curve: svg('<path d="M4 19C6 6 18 18 20 5"/><circle cx="4" cy="19" r="1.5"/><circle cx="20" cy="5" r="1.5"/>'),
  brush: svg('<path d="M14 4l6 6-8 8-6-6z"/><path d="M6 12c-2 1-3 3-3 6 3 0 5-1 6-3"/>'),
  gap: svg('<path d="M4 12a6 6 0 0 1 6-6"/><path d="M14 18a6 6 0 0 0 6-6"/><path d="M10 6l4 12" stroke-dasharray="2 2.5"/>'),
};

// Drawing tools paint the chosen color with a chosen thickness: Line (drag), Curve (drag, then bend
// with its two control points), Brush (freehand, only inside the selected regions) and Close gap (two
// clicks near the ends of an open contour; each end snaps to the nearest pixel of the paint color
// within 2 mm). Line, Curve and Close gap stay editable (drag the points, thickness, angle, length)
// until Apply; every change is previewed with the exact pixels the edit would paint.
const DRAW_MODES = ["line", "curve", "brush", "gap"];
const SHAPE_KINDS = ["line", "curve", "gap"];
const CUBIC_SEGMENTS = 64;
const isDrawOp = (op) => op && (op.op === "paint_stroke" || op.op === "close_gap");
const MAX_BRUSH_POINTS = 4000;

function artOpLabel(op) {
  const w = `${+Number(op.width_mm).toFixed(2)} mm`;
  if (op.op === "close_gap") return `Close gap ${w}${op.path === "cubic" ? " (curved)" : ""}`;
  if (op.op === "paint_stroke") return op.clip_regions ? `Brush ${w} in ${op.clip_regions.length} region(s)` : `${op.path === "cubic" ? "Curve" : "Line"} ${w}`;
  if (op.op === "recolor_regions") return `Recolor ${op.regions.length} region${op.regions.length === 1 ? "" : "s"}`;
  if (op.op === "fit_composition") return `Fit artwork to canvas ${+(op.scale * 100).toFixed(1)} %${op.mode === "manual" ? " (manual)" : ""}`;
  return "Fill";
}

// Centre line of a stroke in artwork pixels ([row, col]); the same cubic sampling as the server.
function centreLine(points, path) {
  if (path !== "cubic") return points;
  const [p0, c1, c2, p3] = points;
  return Array.from({ length: CUBIC_SEGMENTS + 1 }, (_, i) => {
    const t = i / CUBIC_SEGMENTS;
    const a = (1 - t) ** 3, b = 3 * (1 - t) ** 2 * t, c = 3 * (1 - t) * t ** 2, d = t ** 3;
    return [0, 1].map((k) => a * p0[k] + b * c1[k] + c * c2[k] + d * p3[k]);
  });
}

function clampPx(q) {
  const img = $("#art-image");
  const h = img?.naturalHeight || 1e9, w = img?.naturalWidth || 1e9;
  return [Math.min(h - 1, Math.max(0, Math.round(q[0]))), Math.min(w - 1, Math.max(0, Math.round(q[1])))];
}

// Control points that bend a straight segment a -> b into an arc (bulge: fraction of the length).
function bentControls(a, b, bulge = 0.2) {
  const d = [b[0] - a[0], b[1] - a[1]];
  const n = [-d[1] * bulge, d[0] * bulge];
  return [clampPx([a[0] + d[0] / 3 + n[0], a[1] + d[1] / 3 + n[1]]), clampPx([a[0] + (2 * d[0]) / 3 + n[0], a[1] + (2 * d[1]) / 3 + n[1]])];
}

// Angle (degrees, counter-clockwise from "right", screen up = +) and length (mm) of a straight shape.
function shapeGeometry(points, mpp) {
  const [a, b] = [points[0], points[points.length - 1]];
  const angle = (Math.atan2(-(b[0] - a[0]), b[1] - a[1]) * 180) / Math.PI;
  return { angle: Math.round(angle * 10) / 10, length: Math.round(Math.hypot(b[0] - a[0], b[1] - a[1]) * mpp * 100) / 100 };
}

function shapeCard(d, mpp, pv) {
  const title = { line: "Line", curve: "Curve", gap: "Close gap" }[d.kind];
  const pt = (q) => `(${q[0]}, ${q[1]})`;
  const straight = d.path !== "cubic";
  const g = shapeGeometry(d.points, mpp);
  const fields = straight ? `<div class="art-shape-fields">
      <label>Angle <input type="number" id="art-angle" data-testid="art-angle" step="1" value="${g.angle}"> °</label>
      <label>Length <input type="number" id="art-length" data-testid="art-length" min="0.1" step="0.5" value="${g.length}"> mm</label></div>` : "";
  const gapPath = d.kind === "gap" ? `<div class="segmented" role="group" aria-label="Connection">${[["line", "Straight"], ["cubic", "Curved"]].map(([k, l]) => `<button class="tool-btn ${d.path === k ? "active" : ""}" data-action="art-gap-path" data-path="${k}" data-testid="art-gap-${k}" aria-pressed="${d.path === k}">${l}</button>`).join("")}</div>` : "";
  const straighten = d.kind === "curve" ? '<button class="tool-btn" data-action="art-straighten" data-testid="art-straighten">Straighten</button>' : "";
  return `<div class="subpanel art-shape" data-testid="art-shape">
    <div class="art-selection-head"><strong>${title}</strong><span class="small muted" data-testid="art-shape-points">${pt(d.points[0])} → ${pt(d.points[d.points.length - 1])}</span></div>
    <div class="row">${gapPath}${straighten}</div>${fields}
    ${pv ? `<p class="small" style="margin:0" data-testid="art-stroke-info">${esc(artOpLabel(pv.op))}${pv.op.op === "close_gap" ? ` · ends snapped to ${[pv.stroke_px[0], pv.stroke_px[pv.stroke_px.length - 1]].map((q) => `(${q[0]}, ${q[1]})`).join(" and ")}` : ""} · ${fmt(pv.change.pixels)} px · palette ${pv.change.palette_size_before} → ${pv.change.palette_size_after} ${infoTip(pv.notes.join(" "))}</p>` : ""}
    <div class="row"><button class="primary small" data-action="art-done" data-testid="art-done" ${pv ? "" : "disabled"}>${ICON.check}<span>Done</span></button>
      <button class="ghost small" data-action="art-remove" data-testid="art-remove">Remove</button>
      <span class="small muted">Drag the ${straight ? "end points" : "end and control points"} to adjust · Enter: Done</span></div></div>`;
}

// Secondary explanations live in an "i" that shows its text on hover and on keyboard focus.
const infoTip = (text) => `<span class="info-tip tip" tabindex="0" role="note" aria-label="${esc(text)}" data-tip="${esc(text)}">i</span>`;

function artworkSelectionCard(sel, s) {
  if (!sel) {
    const hint = {
      box: "Drag a box on the artwork; then click inside it.",
      fill: "Fill: choose a color, then click a region to fill it.",
      line: "Line: choose a color and thickness, then drag from start to end.",
      curve: "Curve: drag from start to end, then drag the control points to bend it.",
      brush: "Brush: first click the region(s) to paint in (Shift+Click adds); then paint inside them.",
      gap: "Close gap: choose the contour's color and thickness, then click near each end of the gap.",
    }[s.mode] || "Click a region in the artwork to select it.";
    return `<div class="art-empty small muted" data-testid="art-empty">${hint}</div>`;
  }
  const clear = `<button class="art-clear" data-action="art-cancel" data-testid="art-clear-selection">${ICON.close}<span>Clear selection</span></button>`;
  if (sel.regions === 1) {
    return `<div class="subpanel art-selection" data-testid="art-selection">
      <div class="art-selection-head"><div class="row">${swatch(sel.color, true)} <strong>${esc(sel.color)}</strong> <span class="small muted">${fmt(sel.pixels)} px · ${fmt(sel.area_mm2, 2)} mm²</span></div>${clear}</div>
      <p class="small" style="margin:0"><span data-testid="art-selection-count">1 region selected</span> · One connected region of ${sel.regions_of_color} with this color${sel.limited_by_box ? `, limited to the box (${fmt(sel.pixels)} of the region's ${fmt(sel.whole_region_pixels)} px)` : ""}.</p>${brushNote(s)}</div>`;
  }
  const items = sel.items.slice(0, 12).map((i) => `<li>${swatch(i.color)} ${fmt(i.pixels)} px${i.limited_by_box ? " (box)" : ""}</li>`).join("")
    + (sel.items.length > 12 ? `<li class="muted">… ${sel.items.length - 12} more</li>` : "");
  return `<div class="subpanel art-selection" data-testid="art-selection">
    <div class="art-selection-head"><div class="row"><strong data-testid="art-selection-count">${sel.regions} regions selected</strong> ${swatches(sel.colors)} <span class="small muted">${fmt(sel.pixels)} px · ${fmt(sel.area_mm2, 2)} mm²</span></div>${clear}</div>
    ${sel.colors.length > 1 ? `<p class="small" style="margin:0">${sel.colors.length} different original colors; all take the new color together.</p>` : ""}
    <ul class="small art-region-list">${items}</ul>${brushNote(s)}</div>`;
}

const brushNote = (s) => (s.mode === "brush" ? '<p class="small" data-testid="art-brush-note" style="margin:0">The brush paints only inside the selected region(s); nothing outside changes.</p>' : "");

// -- Fit artwork to canvas: enlarge and centre the subject inside the same artwork aperture -----------
// Mouse only: drag the framed subject to move it, drag the bottom-left handle to enlarge or shrink it
// (aspect ratio locked; the opposite top-right corner stays put). The canvas preview is redrawn live
// from the artwork's own pixels (no smoothing); on release the server checks the framing and returns the
// exact edit. The subject always stays inside the artwork aperture. "Fit automatically" restores the
// detected fit; "Apply fit" adds it to the working session like every artwork edit (Save Changes, then
// Plan sheets from this artwork); the sheet size and perimeter never change.
function freshFit() {
  return { open: false, result: null, manual: null, drag: null, loading: false, error: null, seq: 0, acceptLoss: false };
}
const fitSize = (n, s) => Math.max(1, Math.floor(n * s + 1e-9));
const FIT_MIN_PX = 8;  // the subject never shrinks below this many artwork pixels on its shorter side
const FIT_HANDLE_CSS = 14;  // handle size on screen (CSS px)
const FIT_STATUS = {
  READY: ["ok", "Subject found"],
  ALREADY_FITTED: ["neutral", "Already fills the canvas"],
  AMBIGUOUS_BACKGROUND: ["warn", "Background unclear: choose it, then drag the subject"],
  MULTIPLE_FOREGROUND_REGIONS: ["warn", "Several separate pieces: all are kept together"],
  NO_FOREGROUND: ["warn", "No subject found"],
};
const fitImages = {};  // artwork image URL -> loaded Image (pixels drawn into the live preview)

function fitImage(url) {
  if (!url) return null;
  if (!fitImages[url]) {
    const img = new Image();
    img.onload = () => drawFitCanvas();
    img.src = url;
    fitImages[url] = img;
  }
  return fitImages[url].complete && fitImages[url].naturalWidth ? fitImages[url] : null;
}

// Keep the scaled subject inside the canvas: the scale cannot exceed the canvas, the box cannot leave it.
function fitClamp(m, ap) {
  const [r0, c0, r1, c1] = m.source_box_px;
  const bh = r1 - r0, bw = c1 - c0;
  const most = Math.floor(Math.min(ap[0] / bh, ap[1] / bw) * 1e6) / 1e6;
  const least = Math.max(0.05, FIT_MIN_PX / Math.min(bh, bw));
  m.scale = Math.min(most, Math.max(least, m.scale));
  const sh = fitSize(bh, m.scale), sw = fitSize(bw, m.scale);
  m.offset_px = [Math.min(ap[0] - sh, Math.max(0, Math.round(m.offset_px[0]))), Math.min(ap[1] - sw, Math.max(0, Math.round(m.offset_px[1])))];
  return m;
}

async function fitPreview(manual = null) {
  const f = ui.fit;
  const seq = ++f.seq;
  f.loading = true;
  f.error = null;
  render();  // warnings of the previous framing are hidden until this one is checked
  try {
    const r = await artworkCall("fit", manual ? { manual } : {});
    if (seq !== f.seq) return;  // a newer drag already asked again
    f.result = r;
    if (!manual) {
      const start = r.op || r.analysis.manual;
      f.manual = start ? { source_box_px: start.source_box_px, scale: start.scale ?? 1,
                           offset_px: start.offset_px ?? start.source_box_px.slice(0, 2), background: start.background } : null;
      if (f.manual && !r.op) fitClamp(f.manual, r.analysis.aperture_px);
    }
  } catch (err) {
    if (seq !== f.seq) return;
    f.error = err.message || String(err);
  }
  f.loading = false;
  render();
}

// The framing the user dragged, checked by the server (exact pixels, the edit to apply).
function fitCommit() {
  const m = ui.fit.manual;
  if (!m || !m.background) { render(); return; }
  return fitPreview({ source_box_px: m.source_box_px, scale: m.scale, offset_px: m.offset_px, background: m.background,
                      accept_clipping: ui.fit.acceptLoss });
}

const fitSynced = () => {
  const { manual: m, result: r } = ui.fit;
  return Boolean(m && r?.op && r.op.scale === m.scale && r.op.offset_px[0] === m.offset_px[0] && r.op.offset_px[1] === m.offset_px[1]
    && r.op.background === m.background && r.op.source_box_px.every((v, i) => v === m.source_box_px[i])
    && Boolean(r.op.accept_clipping) === Boolean(ui.fit.acceptLoss));
};
// The server's clipping report, only when it describes the framing on screen (never a stale one).
const fitClip = () => (ui.fit.drag || ui.fit.loading || !fitSynced() ? null : ui.fit.result.clipping || null);

async function fitApply() {
  if (!fitSynced()) await fitCommit();
  const r = ui.fit.result;
  if (!r?.can_apply || !r.op || !fitSynced()) return;
  if (!(await finalizeShape())) return;
  if (await artworkAdd(r.op)) {
    ui.fit = freshFit();
    render();
  }
}

// Where the scaled subject is, in canvas (artwork) pixels.
function fitRect(m) {
  const [r0, c0, r1, c1] = m.source_box_px;
  return { x: m.offset_px[1], y: m.offset_px[0], w: fitSize(c1 - c0, m.scale), h: fitSize(r1 - r0, m.scale) };
}

function drawFitCanvas() {
  const canvas = $("#fit-canvas");
  const f = ui.fit;
  if (!canvas || !f.manual) return;
  const ctx = canvas.getContext("2d");
  const ap = f.result.analysis.aperture_px;
  const m = f.manual;
  // The framing shown and the edit the server checked, readable from the page (tests, accessibility tools).
  const clip = fitClip();
  Object.assign(canvas.dataset, { manual: JSON.stringify(m), op: JSON.stringify(f.result.op || null), aperture: JSON.stringify(ap),
                                  loading: f.loading ? "1" : "0", stale: clip ? "0" : "1", clipping: JSON.stringify(clip) });
  const fill = m.background === "frame" ? f.result.frame_color : m.background;
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = fill || "#d9dde3";
  ctx.fillRect(0, 0, ap[1], ap[0]);
  const img = fitImage(ui.state.artwork?.image);
  const [r0, c0, r1, c1] = m.source_box_px;
  const r = fitRect(m);
  if (img) ctx.drawImage(img, c0, r0, c1 - c0, r1 - r0, r.x, r.y, r.w, r.h);
  const px = ap[1] / canvas.getBoundingClientRect().width;  // canvas pixels per CSS pixel
  ctx.lineWidth = 2 * px;
  ctx.setLineDash([6 * px, 4 * px]);
  ctx.strokeStyle = "#ff00ff";
  ctx.strokeRect(r.x, r.y, r.w, r.h);
  ctx.setLineDash([]);
  const hs = FIT_HANDLE_CSS * px;
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#1f2933";
  ctx.fillRect(r.x - hs / 2, r.y + r.h - hs / 2, hs, hs);
  ctx.strokeRect(r.x - hs / 2, r.y + r.h - hs / 2, hs, hs);
  // Subject pushed past a canvas edge: that edge, along the subject, in red (the lost pixels are red on Before).
  if (clip?.new_clipped_px) {
    const band = 6 * px;
    ctx.fillStyle = "rgba(255, 0, 0, 0.85)";
    const x0 = Math.max(0, r.x), x1 = Math.min(ap[1], r.x + r.w), y0 = Math.max(0, r.y), y1 = Math.min(ap[0], r.y + r.h);
    for (const e of clip.clipped_edges) {
      if (e === "top") ctx.fillRect(x0, 0, x1 - x0, band);
      if (e === "bottom") ctx.fillRect(x0, ap[0] - band, x1 - x0, band);
      if (e === "left") ctx.fillRect(0, y0, band, y1 - y0);
      if (e === "right") ctx.fillRect(ap[1] - band, y0, band, y1 - y0);
    }
  }
}

// A drag starts: the last check no longer describes the framing, so its warnings and Apply go away now.
// Hidden in place (visibility), so nothing moves under the pointer while dragging.
function fitMarkStale() {
  document.querySelectorAll('[data-testid="art-fit-warning"], [data-testid="art-fit-clipped-overlay"], [data-testid="art-fit-source-cropped"]')
    .forEach((n) => { n.style.visibility = "hidden"; });
  const apply = $('[data-testid="art-fit-apply"]');
  if (apply) apply.disabled = true;
  const checking = $('[data-testid="art-fit-checking"]');
  if (checking) checking.style.visibility = "visible";
}

function fitPoint(canvas, e) {
  const b = canvas.getBoundingClientRect();
  const ap = ui.fit.result.analysis.aperture_px;
  return [((e.clientY - b.top) / b.height) * ap[0], ((e.clientX - b.left) / b.width) * ap[1]];
}

function bindFit() {
  const canvas = $("#fit-canvas");
  if (!canvas) return;
  drawFitCanvas();
  canvas.addEventListener("pointerdown", (e) => {
    const f = ui.fit;
    if (!f.manual || busy() || e.button !== 0) return;
    const [y, x] = fitPoint(canvas, e);
    const r = fitRect(f.manual);
    const hs = (FIT_HANDLE_CSS * f.result.analysis.aperture_px[1]) / canvas.getBoundingClientRect().width;
    const onHandle = Math.abs(x - r.x) <= hs && Math.abs(y - (r.y + r.h)) <= hs;
    const inside = x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
    if (!onHandle && !inside) return;
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    f.drag = { kind: onHandle ? "resize" : "move", from: [y, x], scale: f.manual.scale, offset: f.manual.offset_px.slice(),
               right: r.x + r.w, top: r.y };
    f.acceptLoss = false;  // an approval covers only the loss it was given for
    fitMarkStale();
  });
  canvas.addEventListener("pointermove", (e) => {
    const f = ui.fit;
    const d = f.drag;
    const [y, x] = fitPoint(canvas, e);
    if (!d) {
      if (!f.manual) return;
      const r = fitRect(f.manual);
      const hs = (FIT_HANDLE_CSS * f.result.analysis.aperture_px[1]) / canvas.getBoundingClientRect().width;
      canvas.style.cursor = Math.abs(x - r.x) <= hs && Math.abs(y - (r.y + r.h)) <= hs ? "nesw-resize"
        : x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h ? "move" : "default";
      return;
    }
    const m = f.manual;
    const ap = f.result.analysis.aperture_px;
    const [r0, c0, r1, c1] = m.source_box_px;
    if (d.kind === "move") {
      m.offset_px = [d.offset[0] + (y - d.from[0]), d.offset[1] + (x - d.from[1])];
    } else {  // bottom-left handle: the top-right corner stays, one factor for both sides
      const bh = r1 - r0, bw = c1 - c0;
      const s = Math.max((d.right - x) / bw, (y - d.top) / bh);
      const most = Math.min(d.right / bw, (ap[0] - d.top) / bh);  // the left and bottom edges stay inside
      m.scale = Math.floor(Math.max(0.05, Math.min(most, s)) * 1e6) / 1e6;
      m.offset_px = [d.top, d.right - fitSize(bw, m.scale)];
    }
    fitClamp(m, ap);
    drawFitCanvas();
  });
  const end = () => {
    if (!ui.fit.drag) return;
    ui.fit.drag = null;
    fitCommit();
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
}

function fitCard() {
  const f = ui.fit;
  const off = busy() ? "disabled" : "";
  if (!f.open) {
    return `<div class="subpanel art-fit closed" data-testid="art-fit">
      <div class="row"><button class="tool-btn" data-action="art-fit-open" data-testid="art-fit-open" ${off}>${ICON.fit}<span>Fit artwork to canvas</span></button>
      ${infoTip("Enlarge and centre the subject inside the same artwork area. Drag it to move it; drag the bottom-left handle to resize it (proportions kept). Exact pixel copies, no new colors; the sheet size does not change, and sheets are planned only when you choose Plan sheets from this artwork.")}</div></div>`;
  }
  const r = f.result;
  const an = r?.analysis;
  const head = `<div class="art-selection-head"><strong>Fit artwork to canvas</strong>
      <button class="art-clear" data-action="art-fit-close" data-testid="art-fit-close">${ICON.close}<span>Close</span></button></div>`;
  if (!an) {
    return `<div class="subpanel art-fit" data-testid="art-fit">${head}
      <p class="small ${f.error ? "no" : "muted"}" style="margin:0" data-testid="art-fit-status">${f.error ? esc(f.error) : "Finding the subject…"}</p></div>`;
  }
  const [kind, label] = FIT_STATUS[an.status] || ["neutral", an.status];
  const m = f.manual;
  const needBackground = m && !m.background;
  const palette = ui.state.artwork.palette || [];
  const background = an.status === "AMBIGUOUS_BACKGROUND" && m ? `<div class="row small" data-testid="art-fit-background">Background
      ${palette.map((c) => `<button class="art-swatch ${m.background === c.hex ? "chosen" : ""}" data-action="art-fit-bg" data-color="${esc(c.hex)}" aria-label="Background ${esc(c.hex)}"><span class="swatch-fill" style="background:${esc(c.hex)}"></span></button>`).join("")}</div>` : "";
  const ap = an.aperture_px;
  const before = ui.state.artwork?.image;
  const clip = fitClip();
  const overlay = clip?.new_clipped_px && r.images?.clipped
    ? `<img class="art-fit-overlay" src="${esc(r.images.clipped)}" alt="pixels that would be cut off" data-testid="art-fit-clipped-overlay">` : "";
  const stage = m ? `<div class="art-fit-stage">
      <figure><div class="art-fit-before"><img src="${esc(before)}" alt="before" data-testid="art-fit-before">${overlay}</div>
        <figcaption class="small muted">Before${overlay ? ' · <span class="no">red: would be cut off</span>' : ""}</figcaption></figure>
      <figure><canvas id="fit-canvas" data-testid="fit-canvas" width="${ap[1]}" height="${ap[0]}" aria-label="Drag the subject to move it; drag the bottom-left handle to resize it"></canvas>
        <figcaption class="small muted">After · drag to move · bottom-left handle to resize</figcaption></figure></div>` : "";
  const warn = clip ? (r.warnings || []).map((w) => `<p class="small no" style="margin:0" data-testid="art-fit-warning">${esc(w)}</p>`).join("") : "";
  const cropped = clip ? Object.entries(clip.source_cropped_edges || {}) : [];
  const note = cropped.length ? `<p class="small muted" style="margin:0" data-testid="art-fit-source-cropped">Already cut off in the original image at the ${
    cropped.map(([e, n]) => `${esc(e)} edge (${n} px)`).join(" and ")}; not caused by this fit.</p>` : "";
  const checking = `<span class="small muted" data-testid="art-fit-checking" style="visibility:${f.loading || (r.op && !clip) ? "visible" : "hidden"}">Checking the framing…</span>`;
  const approve = clip?.needs_approval || clip?.approved
    ? `<button class="tool-btn" data-action="art-fit-accept" data-testid="art-fit-accept">${clip.approved ? "Loss approved: undo approval" : "Approve cutting off the red pixels"}</button>` : "";
  const canApply = r.can_apply && clip && !busy() && !needBackground;
  return `<div class="subpanel art-fit" data-testid="art-fit">${head}
    <p class="small row" style="margin:0"><span data-testid="art-fit-status">${badge(kind, label)}</span>${checking}</p>
    ${background}${stage}${warn}${note}
    ${f.error ? `<p class="small no" style="margin:0">${esc(f.error)}</p>` : ""}
    <div class="row">
      <button class="tool-btn" data-action="art-fit-auto" data-testid="art-fit-auto" ${off}>Fit automatically</button>${approve}
      <button class="primary small" data-action="art-fit-apply" data-testid="art-fit-apply" ${canApply ? "" : "disabled"}>${ICON.check}<span>Apply fit</span></button>
    </div>
  </div>`;
}

function artworkPanel() {
  const a = ui.state.artwork;
  if (!a) return "";
  if (!a.available) return `<div class="panel tight small muted">The artwork editor is unavailable: ${esc(a.reason || "")}</div>`;
  const s = ui.art;
  const dis = busy() ? "disabled" : "";
  const edits = a.edits || [];
  const palette = a.palette || [];
  const drawing = DRAW_MODES.includes(s.mode);
  const painting = drawing || s.mode === "fill";
  const sel = s.selection?.selection;
  const shape = s.draw && SHAPE_KINDS.includes(s.draw.kind) ? s.draw : null;
  const pv = shape && isDrawOp(s.preview?.op) ? s.preview : null;
  const image = pv ? pv.images.after : s.selection ? s.selection.images.selection : a.image;
  const canPick = (sel || painting) && !busy() ? "" : "disabled";
  const u = a.unsaved || { count: 0, redo: 0, ops: [] };
  const savedDis = u.count || busy() ? "disabled" : "";  // the saved history waits for Save / Discard

  const banners = [
    a.differs_from_planned ? `<div class="banner warn" data-testid="art-differs"><strong>Artwork edited after the sheets were planned.</strong> Print-kit export is blocked until you plan sheets from this artwork or undo the edits. ${infoTip(`The current ${a.stage === "review" ? "reviewed stack" : "proposal"} is validated against the previous artwork only, not this one. Planning keeps the current attempt under Previous attempts.`)}</div>` : "",
    a.base_changed ? '<div class="banner info small">The reviewed artwork changed (accepted front view) after these artwork edits started; planning uses the edited artwork shown here.</div>' : "",
    a.planned_from_edited_artwork && !edits.length ? `<p class="small muted" style="margin:0">The current sheets were planned from artwork you edited (${a.planned_from_edited_artwork.region_recolors} artwork edit${a.planned_from_edited_artwork.region_recolors === 1 ? "" : "s"}).</p>` : "",
  ].join("");

  // Tools: select (click / Shift+Click) and the limit box; the eyedropper is a color source below.
  const tool = (m, icon, label, tip) => `<button class="tool-btn tip ${s.mode === m ? "active" : ""}" data-action="art-mode" data-mode="${m}" data-testid="art-mode-${m}" aria-pressed="${s.mode === m}" data-tip="${esc(tip)}" ${dis}>${icon}<span>${label}</span></button>`;
  const tools = `<div class="art-toolbar">
      <div class="segmented" role="group" aria-label="Selection tool">
        ${tool("select", ICON.select, "Select", "Click a region to select it. Shift+Click adds or removes regions, of any colors.")}
        ${tool("box", ICON.box, "Box", "Limit box: drag a box; selections then stay inside it (e.g. one leg when both legs are one region).")}
      </div>
      <div class="segmented" role="group" aria-label="Paint tool">
        ${tool("fill", ICON.fill, "Fill", "Fill: click a region to fill exactly that connected region with the chosen color.")}
        ${tool("line", ICON.line, "Line", "Line: drag a straight line in the chosen color and thickness. A line across a region splits it.")}
        ${tool("curve", ICON.curve, "Curve", "Curve: drag, then bend it with the two control points. A curve across a region splits it.")}
        ${tool("brush", ICON.brush, "Brush", "Brush: select region(s) first, then paint freehand; paint never spills outside them.")}
        ${tool("gap", ICON.gap, "Close gap", "Close gap: click near the two ends of an open contour; the ends snap to the contour and are joined.")}
      </div>
      ${drawing ? `<label class="art-width tip" data-tip="Stroke thickness in mm (the planner's structural width is 1.2 mm)">Thickness
        <input type="number" id="art-width" data-testid="art-width" min="0.1" max="50" step="0.1" value="${esc(String(s.width))}" ${dis}> mm</label>
        ${s.width < 1.2 ? '<span class="small warn-text" data-testid="art-width-warn">thinner than 1.2 mm</span>' : ""}` : ""}
      ${s.box ? `<button class="tool-btn" data-action="art-clear-box" data-testid="art-clear-box">${ICON.close}<span>Clear box</span></button>` : ""}
    </div>`;
  const zoom = zoomBar("art");
  const hint = s.mode === "eyedropper" ? "Click the artwork to sample a color."
    : s.mode === "line" ? (shape ? "Drag an end point to adjust · drag elsewhere for a new line." : "Drag from the start to the end of the line.")
    : s.mode === "curve" ? (shape ? "Drag the end or control points to adjust · drag elsewhere for a new curve." : "Drag from the start to the end of the curve.")
    : s.mode === "brush" ? (s.regions.length ? "Drag to paint inside the selection · Shift+Click adds or removes regions." : "Click the region to paint in first.")
    : s.mode === "gap" ? (s.gap ? `First end at row ${s.gap[0]}, column ${s.gap[1]}: click near the other end.` : "Click near the first end of the gap.")
    : s.mode === "fill" ? "Click a region to fill it."
    : s.box ? `Box: rows ${s.box[0]}–${s.box[2] - 1}, columns ${s.box[1]}–${s.box[3] - 1}.`
    : "Click to select · Shift+Click to add or remove.";

  // New color: palette swatches, any color (the swatch opens the system picker), or sample one.
  const custom = (s.color || (sel && sel.color !== "mixed" ? sel.color : "") || "#888888").toLowerCase();
  const colors = `<div class="art-colors">
      <h4>${painting ? "Paint color" : `New color${sel && sel.regions > 1 ? ` for all ${sel.regions} regions` : ""}`} ${!painting && sel ? '<span class="small muted" style="font-weight:normal">(a click recolors)</span>' : ""}</h4>
      <div class="art-color-row" role="group" aria-label="New color">
        ${palette.map((c) => `<button class="art-swatch tip ${s.color === c.hex ? "chosen" : ""}" data-action="art-color" data-color="${esc(c.hex)}" data-testid="art-palette-color" aria-label="Use ${esc(c.hex)}" aria-pressed="${s.color === c.hex}" data-tip="${esc(c.hex)} · ${fmt(c.area_mm2, 1)} mm² · ${c.regions_4} region(s)" ${canPick}><span class="swatch-fill" style="background:${esc(c.hex)}"></span></button>`).join("")}
        <span class="art-sep" aria-hidden="true"></span>
        <label class="art-custom tip ${s.source === "picker" ? "chosen" : ""}" data-tip="Any color: opens the color picker. A color that is not in the palette adds a filament.">
          <input type="color" id="art-picker" data-testid="art-use-picker" aria-label="Pick any color" value="${esc(custom)}" ${canPick}>
          <span>Custom</span></label>
        <button class="icon-btn tip ${s.mode === "eyedropper" ? "active" : ""}" data-action="art-mode" data-mode="eyedropper" data-testid="art-eyedropper" aria-label="Sample color" aria-pressed="${s.mode === "eyedropper"}" data-tip="Sample color: click anywhere in the artwork to use its exact color" ${canPick}>${ICON.pipette}</button>
      </div>
      <div class="art-chosen" data-testid="art-chosen">${s.color ? `<span class="swatch big" style="background:${esc(s.color)}"></span><strong>${esc(s.color)}</strong><span class="small muted">${esc(ART_SOURCES[s.source] || "")}${s.sampled ? ` at row ${s.sampled[0]}, column ${s.sampled[1]}` : ""}</span>`
        : `<span class="small muted">${painting ? "Choose a color to paint with." : sel ? "Choose a color." : "Select a region first."}</span>`}</div>
    </div>`;

  // One bar for the working session: status, Undo / Redo of single unsaved edits, Discard, Save Changes.
  const status = u.stale ? '<span class="no" data-testid="art-unsaved">Unsaved changes were made on an earlier artwork and cannot be saved; discard them.</span>'
    : u.count ? `<span class="unsaved-dot" aria-hidden="true"></span><strong data-testid="art-unsaved">${u.count} unsaved change${u.count === 1 ? "" : "s"}</strong>${s.recovered ? ' <span class="small muted" data-testid="art-recovered">(recovered)</span>' : ""}`
    : '<span class="small muted" data-testid="art-unsaved">All changes saved</span>';
  const last = s.last ? `<span class="small ${s.last.bad ? "no" : "muted"}" data-testid="art-last">${esc(s.last.text)}</span>` : "";
  const savebar = s.confirmDiscard ? `<div class="art-savebar confirm" data-testid="art-savebar">
      <span><strong>Discard ${u.count} unsaved change${u.count === 1 ? "" : "s"}?</strong> <span class="small muted">The saved artwork stays as it is.</span></span>
      <div class="row"><button class="ghost" data-action="art-discard-cancel" data-testid="art-discard-cancel">Keep editing</button>
        <button class="danger" data-action="art-discard-confirmed" data-testid="art-discard-confirm">Discard</button></div></div>`
    : `<div class="art-savebar ${u.count ? "dirty" : ""}" data-testid="art-savebar">
      <span class="row" style="gap:6px">${status} ${last}</span>
      <div class="row">
        <button class="icon-btn tip" data-action="art-session" data-session="undo" data-testid="art-session-undo" aria-label="Undo" data-tip="Undo the last unsaved change (Ctrl+Z)" ${u.count && !u.stale ? dis : "disabled"}>${ICON.undo}</button>
        <button class="icon-btn tip" data-action="art-session" data-session="redo" data-testid="art-session-redo" aria-label="Redo" data-tip="Redo (Ctrl+Y)" ${u.redo && !u.stale ? dis : "disabled"}><span class="flip">${ICON.undo}</span></button>
        <button class="ghost" data-action="art-discard-ask" data-testid="art-discard" ${u.count || u.stale ? dis : "disabled"}>Discard Changes</button>
        <button class="primary" data-action="art-session" data-session="save" data-testid="art-save" ${(u.count || shape) && !u.stale ? dis : "disabled"}>${ICON.check}<span>Save Changes</span></button>
      </div></div>`;

  const v = a.verify;
  const history = `<div class="stack" style="gap:6px">
      <h4 style="margin:0">Artwork edits ${badge("neutral", String(edits.length))} ${infoTip("Saved edits are kept with the project and reloaded after a restart; Save Changes adds the unsaved ones together. A multi-region recolor is one edit.")}</h4>
      ${edits.length || u.ops.length ? `<ul class="history small" data-testid="art-history">${u.ops.slice().reverse().map((e) => `<li class="unsaved" data-testid="art-history-unsaved"><span>${badge("warn", "unsaved")} ${esc(artOpLabel(e.op))} ${swatches(e.from_colors)} → ${swatch(e.to)} ${fmt(e.selection.pixels)} px</span></li>`).join("")}${edits.slice().reverse().map((e) => `<li><span>#${e.index} ${esc(artOpLabel(e.op))} ${swatches(e.from_colors)} → ${swatch(e.to)} ${fmt(e.selection.pixels)} px${!isDrawOp(e.op) && (e.selection.regions || 1) > 1 ? ` · ${e.selection.regions} regions` : ""}${!isDrawOp(e.op) && e.selection.limited_by_box ? " (box)" : ""} · ${esc(ART_SOURCES[e.op.source] || e.op.source)}${e.change.merged_with_adjacent_regions ? " · merged" : ""}</span>
          <button class="small ghost tip" data-action="art-undo" data-keep="${e.index}" ${e.index === edits.length ? "disabled" : savedDis} data-tip="Keep edits 1..${e.index}">Revert here</button></li>`).join("")}</ul>` : '<p class="small muted" style="margin:0">No artwork edits.</p>'}
      <div class="row"><button class="tool-btn" data-action="art-undo" data-testid="art-undo" ${edits.length ? savedDis : "disabled"}>${ICON.undo}<span>Undo last saved</span></button>
        <button class="tool-btn" data-action="art-undo" data-keep="0" ${edits.length > 1 ? savedDis : "disabled"}>Revert all</button>
        <button class="tool-btn tip" data-action="art-verify" data-testid="art-verify" ${edits.length ? dis : "disabled"} data-tip="Reload the saved log and replay it from its base">Verify save/reload</button></div>
      ${v ? `<p class="small" data-testid="art-verify-result">${v.replay_identical ? `<span class="yes">✓</span> Replaying all ${v.edits} saved edit(s) from the base reproduces the saved artwork exactly.` : `<span class="no">✗</span> ${esc(v.problems.join("; "))}`} <span class="muted">(${esc(v.checked_utc)})</span></p>` : ""}</div>`;

  // Planning from the artwork; also shown where Apply was after a color change.
  const pending = a.can_replan && edits.length > 0 && !u.count;
  const replanNote = `Runs the same automatic proposal (unchanged planner and validators) on the artwork shown here.${a.planned ? " The current attempt (proposal, review, estimate) is archived under Previous attempts." : ""} The palette size and the measured sheet count are shown afterwards; merging colors does not guarantee fewer sheets.`;
  const replanButton = (testid) => `<button class="plan tip" data-action="art-replan" data-testid="${testid}" ${u.count ? 'disabled data-tip="Save Changes first: planning uses the saved artwork"' : dis}>${ICON.plan}<span>Plan sheets from this artwork</span></button>`;
  const callout = pending && !sel && !shape ? `<div class="banner ok art-callout" data-testid="art-try-again-callout">
      <div><strong>${edits.length} artwork edit${edits.length === 1 ? "" : "s"} saved.</strong>
      <span class="small">The sheets are not planned for ${edits.length === 1 ? "it" : "them"} yet. Choose Plan sheets from this artwork to plan ${edits.length === 1 ? "it" : "them"}, or keep editing first.</span></div>
      <div class="row">${replanButton("art-try-again")}</div></div>` : "";
  const replan = a.can_replan ? `<div class="subpanel art-replan" data-testid="art-replan">
      ${replanButton("art-replan-button")}
      <span class="small muted">${u.count ? "Save Changes first." : "Nothing is planned until you click."} ${infoTip(replanNote)}</span></div>` : "";

  return `<details class="panel section art-panel" data-section="artwork" data-testid="artwork-panel" ${openAttr("artwork", true)}>
    <summary><h3>Artwork editor ${badge("neutral", "artwork edit · not a sheet edit")}</h3>
      <span class="small muted">${palette.length ? `${palette.length} colors · ` : ""}${edits.length} edit${edits.length === 1 ? "" : "s"}${u.count ? ` · <span class="unsaved-dot" aria-hidden="true"></span>${u.count} unsaved` : ""}${a.differs_from_planned ? " · not planned yet" : ""}</span></summary>
    <div class="section-body stack">
      <p class="small muted" style="margin:0">Edit the flat-color artwork: recolor and fill regions, draw lines and curves (they split regions), close contour gaps, paint inside a region. Sheets change only when you plan again. ${infoTip("The uploaded image is never changed and no sheet is edited here. Examples: unify two skin tones, change a necklace, or change one leg only with the Box tool.")}</p>
      ${banners}
      ${savebar}
      ${fitCard()}
      <div class="grid2">
        <div class="stack" style="gap:8px">
          ${tools}
          <div class="zoom-viewport art-viewport ${ui.artSpace ? "space" : ""}" id="art-viewport" data-testid="art-viewport" tabindex="0" aria-label="Artwork (mouse wheel zooms; + / - / 0 with focus)">
            <div class="art-stage" id="art-stage"><img id="art-image" src="${esc(image)}" alt="flat-color artwork" draggable="false"></div>
            <canvas id="art-overlay" aria-label="Artwork canvas" data-testid="art-canvas"></canvas>
          </div>
          ${zoom}
          <span class="small muted coords" id="art-coords">${esc(hint)}</span>
        </div>
        <div class="stack art-side">${callout}${shape ? shapeCard(shape, a.mm_per_pixel, pv) : artworkSelectionCard(sel, s)}${colors}</div>
      </div>
      <div class="grid2">
        <div class="stack" style="gap:6px"><h4 style="margin:0">Palette now ${badge("neutral", `${palette.length} colors`)} ${infoTip("One filament color per sheet; the planner decides how many sheets these colors need.")}</h4>
          ${palette.length ? `<ul class="small" data-testid="art-palette" style="margin:0">${palette.map((c) => `<li>${swatch(c.hex)} <code>${esc(c.hex)}</code> · ${fmt(c.area_mm2, 1)} mm² · ${c.regions_4} region${c.regions_4 === 1 ? "" : "s"}</li>`).join("")}</ul>` : '<p class="small muted" style="margin:0">Shown after the first selection.</p>'}</div>
        ${history}
      </div>
      ${replan}
    </div>
  </details>`;
}

// -- zoomable views (Artwork editor, Review layers): viewport zoom only, never the artwork itself.
// The stage is sized image pixels x scale and moved inside a fixed viewport; overlays cover the
// viewport and map through the image's on-screen rectangle, so hit-testing is exact at any zoom.
// Zoom is stored as a multiple of "fit" (null = fit), so it survives switching between images.
const VIEWS = {
  art: { state: () => ui.art, root: "#art-viewport", stage: "#art-stage", image: "#art-image", label: "#art-zoom-label", fitButton: '[data-testid="art-zoom-fit"]', redraw: () => drawArtOverlay() },
  edit: { state: () => ui.editView, root: "#edit-viewport", stage: "#edit-stage", image: "#edit-image", label: "#edit-zoom-label", fitButton: '[data-testid="edit-zoom-fit"]', redraw: () => drawOverlay() },
  insp: { state: () => ui.insp, root: "#insp-viewport", stage: "#insp-stage", image: "#insp-image", label: "#insp-zoom-label", fitButton: '[data-testid="insp-zoom-fit"]', redraw: () => drawInspOverlay(), sync: "inspa" },
  // The processed image beside the layer in the Issue inspector: same sheet extent, zoom and pan follow each other.
  inspa: { state: () => ui.inspA, root: "#insp-art-viewport", stage: "#insp-art-stage", image: "#insp-art-image", label: null, fitButton: null, redraw: () => drawInspOverlay(), sync: "insp" },
};

// Synchronized views (both images span the same sheet extent): the follower gets the leader's zoom
// and the same image point at its centre.
let syncingViews = false;
function syncView(from, to) {
  const a = viewGeometry(from);
  const b = viewGeometry(to);
  if (!a || !b || syncingViews) return;
  syncingViews = true;
  try {
    b.state.zoom = a.state.zoom;
    b.state.free = a.state.free;
    if (a.state.zoom !== null) {
      const fx = (a.vw / 2 - a.state.pan[0]) / (a.img.naturalWidth * a.scale);
      const fy = (a.vh / 2 - a.state.pan[1]) / (a.img.naturalHeight * a.scale);
      const scale = b.fit * b.state.zoom;
      b.state.pan = [b.vw / 2 - fx * b.img.naturalWidth * scale, b.vh / 2 - fy * b.img.naturalHeight * scale];
    }
    // The pan is set for the follower's current size: no "keep the centre after a resize" correction.
    b.state.lastView = { vw: b.vw, vh: b.vh, scale: b.fit * (b.state.zoom ?? 1) };
    applyView(to);
  } finally {
    syncingViews = false;
  }
}
const ZOOM_MAX = 40;  // x the fitted size

function viewGeometry(key) {
  const v = VIEWS[key];
  const vp = $(v.root);
  const img = $(v.image);
  // A viewport that is not laid out (0 x 0, e.g. while the page is captured or a section is closed) is
  // left alone: a zero scale would be stored as the last view and turn the next pan into NaN.
  if (!vp || !img || !img.naturalWidth || !vp.clientWidth || !vp.clientHeight) return null;
  const vw = vp.clientWidth;
  const vh = vp.clientHeight;
  const fit = Math.min(vw / img.naturalWidth, vh / img.naturalHeight);
  const state = v.state();
  return { v, vp, img, vw, vh, fit, state, scale: fit * (state.zoom ?? 1) };
}

function applyView(key) {
  const g = viewGeometry(key);
  if (!g) return;
  const last = g.state.lastView;  // the viewport can change size (window resize): keep the same centre
  if (g.state.zoom !== null && last && (last.vw !== g.vw || last.vh !== g.vh)) {
    const cx = (last.vw / 2 - g.state.pan[0]) / last.scale;
    const cy = (last.vh / 2 - g.state.pan[1]) / last.scale;
    g.state.pan = [g.vw / 2 - cx * g.scale, g.vh / 2 - cy * g.scale];
  }
  g.state.lastView = { vw: g.vw, vh: g.vh, scale: g.scale };
  const w = g.img.naturalWidth * g.scale;
  const h = g.img.naturalHeight * g.scale;
  const clamp = (p, size, room) => (g.state.free && g.state.zoom !== null ? Math.min(room / 2, Math.max(room / 2 - size, p))
    : size <= room ? (room - size) / 2 : Math.min(0, Math.max(room - size, p)));
  g.state.pan = [clamp(g.state.zoom === null ? 0 : g.state.pan[0], w, g.vw), clamp(g.state.zoom === null ? 0 : g.state.pan[1], h, g.vh)];
  const stage = $(g.v.stage);
  stage.style.width = `${w}px`;
  stage.style.height = `${h}px`;
  stage.style.transform = `translate(${g.state.pan[0]}px, ${g.state.pan[1]}px)`;
  const label = g.v.label && $(g.v.label);
  if (label) label.textContent = `${Math.round(g.scale * 100)}%`;
  if (g.v.fitButton) $(g.v.fitButton)?.classList.toggle("active", g.state.zoom === null);
  g.v.redraw();
  if (g.v.sync && !syncingViews) syncView(key, g.v.sync);
}

// Zoom by a factor (or "fit"), keeping the image point under the screen point (cx, cy) still
// (default: the viewport centre).  The correction is measured on the rendered image, so sub-pixel
// offsets cannot accumulate over many wheel steps.
function zoomView(key, how, cx, cy) {
  const g = viewGeometry(key);
  if (!g) return;
  const state = g.state;
  if (how === "fit") { state.zoom = null; state.free = false; applyView(key); return; }
  const zoom = Math.min(ZOOM_MAX, Math.max(1, (state.zoom ?? 1) * how));
  if (zoom <= 1 + 1e-6) { state.zoom = null; state.free = false; applyView(key); return; }
  const vr = g.vp.getBoundingClientRect();
  const x = cx ?? vr.left + vr.width / 2;
  const y = cy ?? vr.top + vr.height / 2;
  const before = g.img.getBoundingClientRect();
  const fx = (x - before.left) / before.width;
  const fy = (y - before.top) / before.height;
  if (state.zoom === null) state.pan = [before.left - vr.left - g.vp.clientLeft, before.top - vr.top - g.vp.clientTop];
  const ratio = zoom / (state.zoom ?? 1);
  state.zoom = zoom;
  state.pan = [x - vr.left - g.vp.clientLeft - (x - before.left) * ratio, y - vr.top - g.vp.clientTop - (y - before.top) * ratio];
  applyView(key);
  const after = g.img.getBoundingClientRect();
  const dx = x - (after.left + fx * after.width);
  const dy = y - (after.top + fy * after.height);
  if (Math.abs(dx) > 0.01 || Math.abs(dy) > 0.01) {
    state.pan = [state.pan[0] + dx, state.pan[1] + dy];
    applyView(key);
  }
}

// Wheel zoom toward the cursor (no modifier), middle-drag or Space+drag panning, and keyboard zoom
// (+ / - / 0, arrows pan) when the viewport has focus.  Registered before a view's own tool
// handlers: a pan consumes the pointer events, so it never selects, samples or draws.
function bindViewport(key, canvas) {
  const vp = $(VIEWS[key].root);
  const state = VIEWS[key].state();
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();  // the page does not scroll while the cursor is over the artwork
    const delta = Math.max(-300, Math.min(300, e.deltaY * (e.deltaMode === 1 ? 16 : 1)));
    zoomView(key, Math.exp(-delta * 0.0022), e.clientX, e.clientY);
  }, { passive: false });
  canvas.addEventListener("pointerdown", (e) => {
    if (!(e.button === 1 || (e.button === 0 && ui.artSpace))) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    canvas.setPointerCapture(e.pointerId);
    state.panning = { x: e.clientX, y: e.clientY, pan: [...state.pan] };
    vp.classList.add("panning");
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!state.panning) return;
    e.stopImmediatePropagation();
    const p = state.panning;
    if (state.zoom !== null) state.pan = [p.pan[0] + e.clientX - p.x, p.pan[1] + e.clientY - p.y];
    applyView(key);
  });
  canvas.addEventListener("pointerup", (e) => {
    if (!state.panning) return;
    e.stopImmediatePropagation();
    state.panning = null;
    vp.classList.remove("panning");
  });
  canvas.addEventListener("auxclick", (e) => e.preventDefault());
  vp.addEventListener("keydown", (e) => {
    const step = { "+": 1.25, "=": 1.25, "-": 0.8, _: 0.8 }[e.key];
    const move = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] }[e.key];
    if (step) zoomView(key, step);
    else if (e.key === "0") zoomView(key, "fit");
    else if (move && state.zoom !== null) { state.pan = [state.pan[0] + move[0], state.pan[1] + move[1]]; applyView(key); }
    else return;
    e.preventDefault();
  });
}

// Compact zoom readout: percentage, Fit, and the help text in a tooltip.
function zoomBar(key) {
  return `<div class="zoom-bar" role="group" aria-label="Zoom">
      <span class="zoom-label" id="${key}-zoom-label" data-testid="${key}-zoom-label" aria-live="polite">fit</span>
      <button class="tool-btn tip" data-action="zoom-fit" data-view-key="${key}" data-testid="${key}-zoom-fit" data-tip="Fit to view: show the whole image (reset zoom and position)">${ICON.fit}<span>Fit</span></button>
      ${infoTip("Mouse wheel over the image zooms toward the cursor. Space + drag, or drag with the middle mouse button, moves the view. With the image focused: + and - zoom, 0 fits, arrows move.")}
    </div>`;
}

const applyArtView = () => applyView("art");
const zoomArt = (how, vx, vy) => zoomView("art", how, vx, vy);

function artPx(event) {
  const img = $("#art-image");
  const r = img.getBoundingClientRect();
  const w = img.naturalWidth || 1;
  const h = img.naturalHeight || 1;
  return [Math.min(h - 1, Math.max(0, Math.floor(((event.clientY - r.top) / r.height) * h))),
          Math.min(w - 1, Math.max(0, Math.floor(((event.clientX - r.left) / r.width) * w)))];
}

function artBox(a, b) {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]) + 1, Math.max(a[1], b[1]) + 1];
}

function drawArtOverlay() {
  const img = $("#art-image");
  const canvas = $("#art-overlay");
  if (!img || !canvas || !img.naturalWidth) return;
  const c = canvas.getBoundingClientRect();
  const r = img.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(c.width * dpr));
  const h = Math.max(1, Math.round(c.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  const sx = (r.width / img.naturalWidth) * dpr;
  const sy = (r.height / img.naturalHeight) * dpr;
  const ox = (r.left - c.left) * dpr;
  const oy = (r.top - c.top) * dpr;
  const box = ui.art.drag ? artBox(ui.art.drag.from, ui.art.drag.to) : ui.art.box;
  ctx.lineWidth = 2 * dpr;
  if (box) {
    ctx.strokeStyle = "#00a0ff";
    ctx.setLineDash([6 * dpr, 4 * dpr]);
    ctx.strokeRect(ox + box[1] * sx, oy + box[0] * sy, (box[3] - box[1]) * sx, (box[2] - box[0]) * sy);
    ctx.setLineDash([]);
  }
  if (ui.art.focus) {  // the issue opened from the inspector ([row0, col0, row1, col1])
    const [r0, c0, r1, c1] = ui.art.focus;
    ctx.save();
    ctx.strokeStyle = "#e0245e";
    ctx.lineWidth = 2 * dpr;
    ctx.setLineDash([5 * dpr, 3 * dpr]);
    const pad = 4 * dpr;
    ctx.strokeRect(ox + c0 * sx - pad, oy + r0 * sy - pad, (c1 - c0) * sx + 2 * pad, (r1 - r0) * sy + 2 * pad);
    ctx.restore();
  }
  const stroke = ui.art.stroke;
  const mpp = ui.state?.artwork?.mm_per_pixel;
  if (stroke && stroke.points.length && mpp) {  // the stroke being drawn, at its real thickness
    ctx.save();
    ctx.globalAlpha = 0.7;
    ctx.strokeStyle = ctx.fillStyle = ui.art.color || "#ff2d55";
    ctx.lineWidth = Math.max(1, (ui.art.width / mpp) * sx);
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.beginPath();
    stroke.points.forEach((q, i) => ctx[i ? "lineTo" : "moveTo"](ox + (q[1] + 0.5) * sx, oy + (q[0] + 0.5) * sy));
    if (stroke.points.length === 1) ctx.lineTo(ox + (stroke.points[0][1] + 0.5) * sx + 0.01, oy + (stroke.points[0][0] + 0.5) * sy);
    ctx.stroke();
    ctx.restore();
  }
  const d = ui.art.draw;
  if (d && SHAPE_KINDS.includes(d.kind)) {  // the pending shape: centre line, handles (and the live stroke while dragging)
    const at = (q) => [ox + (q[1] + 0.5) * sx, oy + (q[0] + 0.5) * sy];
    const line = centreLine(d.points, d.path);
    const trace = (width, color, dash) => {
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.lineCap = ctx.lineJoin = "round";
      ctx.setLineDash(dash);
      ctx.beginPath();
      line.forEach((q, i) => ctx[i ? "lineTo" : "moveTo"](...at(q)));
      ctx.stroke();
      ctx.restore();
    };
    if (ui.art.handle !== null && mpp) {
      ctx.globalAlpha = 0.7;
      trace(Math.max(1, (ui.art.width / mpp) * sx), ui.art.color || "#ff2d55", []);
      ctx.globalAlpha = 1;
    }
    trace(1.5 * dpr, "#00a0ff", [5 * dpr, 4 * dpr]);
    if (d.path === "cubic") {  // tangents end -> control
      ctx.save();
      ctx.strokeStyle = "#ff9500";
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.moveTo(...at(d.points[0])); ctx.lineTo(...at(d.points[1]));
      ctx.moveTo(...at(d.points[3])); ctx.lineTo(...at(d.points[2]));
      ctx.stroke();
      ctx.restore();
    }
    d.points.forEach((q, i) => {
      const [x, y] = at(q);
      const control = d.path === "cubic" && (i === 1 || i === 2);
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = control ? "#ff9500" : "#0070f0";
      ctx.lineWidth = 2 * dpr;
      ctx.beginPath();
      if (control) ctx.rect(x - 5 * dpr, y - 5 * dpr, 10 * dpr, 10 * dpr);
      else ctx.arc(x, y, 6 * dpr, 0, Math.PI * 2);
      ctx.fill(); ctx.stroke();
    });
  }
  if (ui.art.gap && mpp) {  // first gap end and its 2 mm snap radius
    ctx.strokeStyle = "#ff9500";
    ctx.setLineDash([4 * dpr, 3 * dpr]);
    ctx.beginPath(); ctx.arc(ox + (ui.art.gap[1] + 0.5) * sx, oy + (ui.art.gap[0] + 0.5) * sy, (2 / mpp) * sx, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
  }
  const marks = [...ui.art.regions.map((reg) => [reg.at_px, "#ff2d55"]), [ui.art.sampled, "#00a86b"], [ui.art.gap, "#ff9500"]];
  for (const [point, color] of marks) {
    if (!point) continue;
    ctx.strokeStyle = color;
    ctx.beginPath(); ctx.arc(ox + (point[1] + 0.5) * sx, oy + (point[0] + 0.5) * sy, 6 * dpr, 0, Math.PI * 2); ctx.stroke();
  }
}

function bindArtwork() {
  const canvas = $("#art-overlay");
  if (!canvas) return;
  bindViewport("art", canvas);
  $("#art-image")?.addEventListener("load", applyArtView);
  applyArtView();
  canvas.addEventListener("pointerdown", (e) => {
    if (busy() || e.button !== 0) return;
    const s = ui.art;
    const point = artPx(e);
    const handle = shapeHandleAt(e);
    if (handle !== null) {  // adjust the pending line, curve or gap closure
      canvas.setPointerCapture(e.pointerId);
      s.handle = handle;
      showCurrentArtwork();
      drawArtOverlay();
    } else if (s.mode === "box") {
      canvas.setPointerCapture(e.pointerId);
      s.drag = { from: point, to: point };
    } else if (s.mode === "line" || s.mode === "curve" || (s.mode === "brush" && s.regions.length && !e.shiftKey)) {
      if (!s.color) { artNotice("Choose a paint color first."); return; }
      canvas.setPointerCapture(e.pointerId);
      s.stroke = { points: s.mode === "brush" ? [point] : [point, point] };
      drawArtOverlay();
    }
  });
  canvas.addEventListener("pointermove", (e) => {
    const point = artPx(e);
    const c = $("#art-coords");
    if (c) c.textContent = `row ${point[0]} · column ${point[1]} (artwork pixels)`;
    if (ui.art.drag) { ui.art.drag.to = point; drawArtOverlay(); }
    if (ui.art.handle !== null && ui.art.draw) { ui.art.draw.points[ui.art.handle] = point; drawArtOverlay(); return; }
    const stroke = ui.art.stroke;
    if (stroke) {
      const last = stroke.points[stroke.points.length - 1];
      if (ui.art.mode !== "brush") stroke.points[1] = point;
      else if ((last[0] !== point[0] || last[1] !== point[1]) && stroke.points.length < MAX_BRUSH_POINTS) stroke.points.push(point);
      drawArtOverlay();
    }
  });
  canvas.addEventListener("pointerup", async (e) => {
    if (busy() || e.button !== 0) return;
    const s = ui.art;
    const point = artPx(e);
    if (s.handle !== null) {
      s.handle = null;
      await artworkDraw();
      return;
    }
    if (s.stroke) {
      const points = s.stroke.points;
      s.stroke = null;
      if (s.mode === "brush") { await artworkBrush(points); return; }
      const [a, b] = points;
      if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 2) { render(); artNotice("Drag from the start to the end."); return; }
      if (!(await finalizeShape())) return;  // a new shape: the previous one becomes an edit first
      await artworkDraw(s.mode === "curve" ? { kind: "curve", path: "cubic", points: [a, ...bentControls(a, b), b] }
        : { kind: "line", path: "line", points: [a, b] });
      return;
    }
    if (s.mode === "gap") {
      if (!s.color) { artNotice("Choose the contour's color first."); return; }
      if (s.draw && !(await finalizeShape())) return;
      const g = ui.art;
      if (!g.gap) { g.gap = point; drawArtOverlay(); artNotice(`First end at row ${point[0]}, column ${point[1]}: click near the other end.`); return; }
      const first = g.gap;
      g.gap = null;
      await artworkDraw({ kind: "gap", path: "line", points: [first, point] });
      return;
    }
    if (s.mode === "line" || s.mode === "curve") return;
    if (s.drag) {
      const box = artBox(ui.art.drag.from, point);
      ui.art.drag = null;
      ui.art.box = box[2] - box[0] > 1 && box[3] - box[1] > 1 ? box : null;
      ui.art.mode = "select";
      render();
      return;
    }
    if (s.mode === "eyedropper") await artworkSample(point);
    else if (s.mode === "fill") await artworkFill(point);
    else await artworkSelect(point, e.shiftKey);
  });
}

// Index of the pending shape's point under the cursor (within 9 screen px), or null.
function shapeHandleAt(event) {
  const d = ui.art.draw;
  const img = $("#art-image");
  if (!d || !SHAPE_KINDS.includes(d.kind) || !img?.naturalWidth) return null;
  const r = img.getBoundingClientRect();
  let best = null, bestDist = 9;
  d.points.forEach((q, i) => {
    const x = r.left + ((q[1] + 0.5) / img.naturalWidth) * r.width;
    const y = r.top + ((q[0] + 0.5) / img.naturalHeight) * r.height;
    const dist = Math.hypot(event.clientX - x, event.clientY - y);
    if (dist <= bestDist) { best = i; bestDist = dist; }
  });
  return best;
}

// While a shape point is dragged the artwork is shown without the previewed stroke.
function showCurrentArtwork() {
  const img = $("#art-image");
  const base = ui.state.artwork?.image;
  if (img && base && !img.src.endsWith(base)) img.src = base;
}

// Drop the pending shape (and its preview) before a new one starts.
function discardShape(rerender = false) {
  const s = ui.art;
  if (!s.draw || !SHAPE_KINDS.includes(s.draw.kind)) return;
  s.draw = null;
  if (isDrawOp(s.preview?.op)) s.preview = null;
  if (rerender) render(); else showCurrentArtwork();
}

function artNotice(text) {
  const c = $("#art-coords");
  if (c) c.textContent = text;
}

// Space held (not while typing) turns a left drag on the artwork into panning.
function onArtKey(event) {
  if (event.code !== "Space" || !$(".zoom-viewport")) return;
  if (event.target.closest?.("input, textarea, select, button, summary")) return;
  const down = event.type === "keydown";
  if (down) event.preventDefault();  // no page scroll while panning
  if (ui.artSpace === down) return;
  ui.artSpace = down;
  document.querySelectorAll(".zoom-viewport").forEach((vp) => vp.classList.toggle("space", down));
}

// Inside the Artwork editor (not while typing): Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo / redo one unsaved
// change; Enter finishes and Escape removes the pending Line / Curve / Close gap.
async function onArtShortcut(event) {
  if (!event.target.closest?.('[data-testid="artwork-panel"]') || event.target.closest("input, textarea, select")) return;
  const ctrl = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  const shape = ui.art.draw && SHAPE_KINDS.includes(ui.art.draw.kind);
  let act = null;
  if (ctrl && key === "z" && !event.shiftKey) act = () => artworkSession("undo");
  else if (ctrl && (key === "y" || (key === "z" && event.shiftKey))) act = () => artworkSession("redo");
  else if (!ctrl && event.key === "Enter" && shape && !event.target.closest("button")) act = finalizeShape;
  else if (!ctrl && event.key === "Escape" && shape) act = () => discardShape(true);
  if (!act) return;
  event.preventDefault();
  await act();
}

// Keyboard focus inside the Artwork colors panel survives a re-render (e.g. after choosing a color),
// so keyboard users continue where they were instead of at the top of the page.
const ART_FOCUS_ATTRS = ["data-testid", "data-action", "data-mode", "data-color", "data-zoom", "data-view", "data-keep"];
function artFocusKey() {
  const el = document.activeElement;
  if (!el || !el.closest?.('[data-testid="artwork-panel"]')) return null;
  if (el.id) return `#${el.id}`;
  const parts = ART_FOCUS_ATTRS.filter((a) => el.hasAttribute(a)).map((a) => `[${a}="${CSS.escape(el.getAttribute(a))}"]`);
  return parts.length ? `[data-testid="artwork-panel"] ${el.tagName.toLowerCase()}${parts.join("")}` : null;
}
function restoreArtFocus(key) {
  if (!key) return;
  const el = document.querySelector(key);
  // A control that is now disabled cannot take focus; fall back to the panel's next useful action.
  const target = el && !el.disabled ? el : document.querySelector('[data-testid="art-save"]:not([disabled]), [data-testid="art-try-again"], [data-testid="art-clear-selection"]');
  target?.focus({ preventScroll: true });
}

function artworkCall(action, body) {
  return api("POST", `/api/projects/${encodeURIComponent(ui.name)}/artwork/${action}`, body);
}

// The view (zoom, pan), tool, thickness and limit box survive a new selection, Apply, Undo and
// Cancel; the paint tools also keep their color, so several strokes or fills follow one another.
function keepArtView(extra = {}) {
  const s = ui.art;
  const mode = s.mode === "eyedropper" ? s.prevMode || "select" : s.mode;
  const paint = DRAW_MODES.includes(mode) || mode === "fill";
  return { ...freshArt(), mode, box: s.box, zoom: s.zoom, pan: s.pan, width: s.width, recovered: false, focus: s.focus,
           ...(paint ? { color: s.color, source: s.source, sampled: s.sampled } : {}), ...extra };
}

// Preview a shape: Line, Curve or Close gap. ``spec`` ({kind, points, path}) starts a new shape;
// without it the pending one is previewed again (moved points, new color, thickness, angle or
// length). The shape stays pending (editable) until Done, the next shape, a tool change or Save.
async function artworkDraw(spec) {
  const s = ui.art;
  ui.error = null;
  if (spec) s.draw = { path: "line", ...spec, op: spec.kind === "gap" ? "close_gap" : "paint_stroke" };
  const d = s.draw;
  if (!d) return;
  const body = { op: d.op, points_px: d.points, width_mm: s.width, color: s.color, source: s.source };
  if (d.path === "cubic") body.path = "cubic";
  if (s.source === "eyedropper") body.sampled_at_px = s.sampled;
  try {
    s.preview = await artworkCall("preview", body);
    if (d.kind === "gap") {  // the ends snapped to the contour: the handles follow
      const snapped = s.preview.stroke_px;
      d.points[0] = snapped[0];
      d.points[d.points.length - 1] = snapped[snapped.length - 1];
    }
  } catch (err) {
    s.preview = null;  // the shape stays pending so its points can be moved
    render();
    artNotice(`Not drawn: ${err.message || err}`);
    return;
  }
  render();
}

// One completed edit into the working session (shown at once, saved with Save Changes).
async function artworkAdd(op, extra = {}) {
  if (busy()) return false;
  ui.error = null;
  try {
    const result = await artworkCall("session/add", { op });
    ui.art = keepArtView({ ...extra, last: { text: `${artOpLabel(op)}: ${fmt(result.change.pixels)} px changed` } });
    await loadState();
  } catch (err) {
    ui.art.last = { text: `Not changed: ${err.message || err}`, bad: true };
    render();
    return false;
  }
  render();
  return true;
}

// The pending Line / Curve / Close gap becomes an edit (its last preview). Without a valid preview it
// is dropped. Returns false when the edit was refused (the shape then stays for correction).
async function finalizeShape() {
  const s = ui.art;
  if (!s.draw || !SHAPE_KINDS.includes(s.draw.kind)) return true;
  const pv = s.preview;
  if (!pv || !isDrawOp(pv.op)) { s.draw = null; return true; }
  return artworkAdd(pv.op);
}

function opColor(extra) {
  const s = ui.art;
  const op = { ...extra, color: s.color, source: s.source };
  if (s.source === "eyedropper") op.sampled_at_px = s.sampled;
  return op;
}

async function artworkFill(at) {
  if (!ui.art.color) { artNotice("Choose a paint color first."); return; }
  await artworkAdd(opColor({ op: "recolor_region", at_px: at, ...(ui.art.box ? { box_px: ui.art.box } : {}) }));
}

// Brush: paint inside the selected regions; the selection stays for the next stroke.
async function artworkBrush(points) {
  const regions = ui.art.regions.slice();
  const op = opColor({ op: "paint_stroke", points_px: points, width_mm: ui.art.width, clip_regions: regions });
  if (!(await artworkAdd(op))) { drawArtOverlay(); return; }
  try {
    const result = await artworkCall("preview", { regions });
    ui.art.regions = result.regions;
    ui.art.selection = result.selection ? result : null;
  } catch { /* the painted region no longer exists as selected: choose again */ }
  render();
}

async function artworkSession(action) {
  if (busy()) return;
  if (action === "save" && !(await finalizeShape())) return;
  if (action === "undo" && ui.art.draw && SHAPE_KINDS.includes(ui.art.draw.kind)) { discardShape(true); return; }
  ui.error = null;
  try {
    const result = await artworkCall(`session/${action}`, {});
    const text = action === "save" ? `Saved ${result.saved} change${result.saved === 1 ? "" : "s"}`
      : action === "discard" ? `Discarded ${result.discarded} change${result.discarded === 1 ? "" : "s"}` : null;
    ui.art = keepArtView(text ? { last: { text } } : {});
    await loadState();
  } catch (err) {
    showError(err, action === "save" ? "Save Changes" : `Artwork ${action}`);
    return;
  }
  render();
  if (action === "save") $('[data-testid="art-try-again-callout"]')?.scrollIntoView({ block: "center" });
}

// Angle / length fields of a straight pending shape: the start stays, the end moves.
function setShapeGeometry(angle, length) {
  const d = ui.art.draw;
  const mpp = ui.state.artwork?.mm_per_pixel;
  if (!d || d.path === "cubic" || !mpp) return;
  const a = d.points[0];
  const px = length / mpp;
  const rad = (angle * Math.PI) / 180;
  d.points[d.points.length - 1] = clampPx([a[0] - Math.sin(rad) * px, a[1] + Math.cos(rad) * px]);
  artworkDraw();
}

// Click: select the region at the point (replacing the selection). Shift+Click: add the region, or
// remove it when it is already selected. The server resolves regions and returns the new list.
async function artworkSelect(at, additive = false) {
  ui.error = null;
  const s = ui.art;
  try {
    const result = await artworkCall("preview", { regions: additive ? s.regions : [], toggle: { at_px: at, box_px: s.box } });
    s.regions = result.regions;
    s.preview = null;
    s.selection = result.selection ? result : null;
    s.draw = null;
  } catch (err) {
    showError(err, "Artwork selection");
    return;
  }
  render();
}

async function artworkChoose(color, source, sampled = null) {
  ui.error = null;
  Object.assign(ui.art, { color, source, sampled });
  if (ui.art.draw) { await artworkDraw(); return; }  // same shape, new color
  if (!ui.art.regions.length || DRAW_MODES.includes(ui.art.mode) || ui.art.mode === "fill") { render(); return; }
  await artworkAdd(opColor({ op: "recolor_regions", regions: ui.art.regions }), { color, source, sampled });  // recolor the selection now
}

async function artworkSample(at) {
  try {
    const sampled = await artworkCall("sample", { at_px: at });
    ui.art.mode = ui.art.prevMode || "select";
    await artworkChoose(sampled.color, "eyedropper", sampled.at_px);
  } catch (err) {
    showError(err, "Eyedropper");
  }
}

async function artworkPost(action, body) {
  if (busy()) return;
  try {
    await artworkCall(action, body);
    if (action !== "verify") ui.art = keepArtView();
    await loadState();
  } catch (err) {
    showError(err, `Artwork ${action}`);
    return;
  }
  render();
}

// ----------------------------------------------------------------------- issue inspector
// Located problems drawn on the physical sheet they belong to (or on the artwork for intake and
// planner findings, never on a sheet). One layer is shown at a time; the strip above the image picks
// it and counts its issues. Clicking an outline, a numbered marker or a list row zooms onto it; Edit
// layer / Edit artwork open the existing editors there. The inspector never edits: it reads /issues,
// which the server computes from the existing checks (sheet validation and the FDM geometry check of
// the shown sheets, or a current saved estimate) and the proposal's saved evidence.
const SEV_CLASS = { FAIL: "bad", BLOCKER: "bad", UNSUPPORTED: "bad", RISK: "warn", WARN: "warn", INFO: "neutral" };
const SEV_COLOR = { FAIL: "#ff1f5a", BLOCKER: "#ff1f5a", UNSUPPORTED: "#ff1f5a", RISK: "#ff7a00", WARN: "#f5c400", INFO: "#2f8cff" };
const SEV_ORDER = ["FAIL", "BLOCKER", "UNSUPPORTED", "RISK", "WARN", "INFO"];
const ISSUE_SOURCES = { validation: ["validation", "candidate"], fdm_estimate: ["fdm_estimate"], geometry_check: ["geometry_check"],
  planner: ["proposal"], intake: ["proposal"] };

function freshInsp() {
  return { selected: null, selectedKey: null, kind: "", severity: "", status: "", view: "auto", zoom: null, pan: [0, 0], panning: null, shown: null };
}

async function loadIssues() {
  if (!ui.name || ui.issuesLoading || !ui.state?.proposal || busy()) return;
  ui.issuesLoading = true;
  ui.issuesDirty = false;
  const name = ui.name;
  const same = (a, b) => JSON.stringify({ ...a, computed_utc: null }) === JSON.stringify({ ...b, computed_utc: null });
  let changed = true;
  const wasStale = Boolean(ui.issues && !ui.issues.error && inspBasisStale(ui.issues));
  if (wasStale) render();  // show "re-checking" on the old locations while the check runs
  try {
    const data = await api("GET", `/api/projects/${encodeURIComponent(name)}/issues`);
    if (ui.name !== name) return;
    changed = wasStale || !ui.issues || !same(ui.issues, data);
    ui.issues = data;
    keepIssueSelection(data);
  } catch (err) {
    if (ui.name === name) ui.issues = { error: err.message || String(err) };
  } finally {
    ui.issuesLoading = false;
  }
  if (changed) render();  // nothing new: no re-render (it would interrupt a drag or a pan in progress)
}

// Issue numbers follow the current result; the selection follows the issue's stable key across
// recomputations (or is dropped when the issue is gone).
function keepIssueSelection(data) {
  if (!ui.insp.selectedKey || !data?.issues) return;
  const same = data.issues.find((i) => i.key === ui.insp.selectedKey);
  ui.insp.selected = same ? same.id : null;
  if (!same) ui.insp.selectedKey = null;
}

// Review decisions (Needs review / Fix later / Accept risk): saved on the server with the issue's
// fingerprints; they never change the geometry, the severity or the warning itself.
const REVIEW_STATES = [
  ["needs_review", "Needs review", "Not decided yet."],
  ["fix_later", "Fix later", "Keep it on the to-do list. Nothing is changed."],
  ["accept_risk", "Accept risk", "Print as it is. The geometry and this warning stay unchanged. The decision lapses if the image, the layer or the print settings change here."],
];
const REVIEW_LABEL = Object.fromEntries(REVIEW_STATES.map(([s, l]) => [s, l]));

async function inspReview(keys, state) {
  const name = ui.name;
  try {
    const data = await api("POST", `/api/projects/${encodeURIComponent(name)}/issues/review`, { keys, state });
    if (ui.name !== name) return;
    ui.issues = data;
    keepIssueSelection(data);
  } catch (err) {
    showError(err, "Issue review");
    return;
  }
  render();
}

// The processed image padded with the perimeter band, so it spans the same sheet extent (and the same
// pixel grid) as the layer images: one view transform serves both. Built once per image.
function paddedArtwork(d) {
  const art = d.images.find((i) => i.key === "artwork");
  if (!art) return "";
  if (ui.inspPad?.src === art.url) return ui.inspPad.url || "";
  if (ui.inspPad?.url) URL.revokeObjectURL(ui.inspPad.url);
  const pad = { src: art.url, url: null };
  ui.inspPad = pad;
  const img = new Image();
  img.onload = () => {
    const band = Math.round(d.grid.perimeter_pixels * (img.naturalWidth / d.grid.artwork_pixels));
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth + 2 * band;
    canvas.height = img.naturalHeight + 2 * band;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#b9bdc4";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, band, band);
    canvas.toBlob((blob) => {
      if (ui.inspPad !== pad || !blob) return;
      pad.url = URL.createObjectURL(blob);
      const el = $("#insp-art-image");
      if (el) el.src = pad.url;
    }, "image/png");
  };
  img.src = art.url;
  return "";
}

// The sheets the shown issues were computed for, and the sheets the state has now: after a layer edit
// (or a new review) the old locations are drawn as out of date until the checks have run again.
function currentIssueBasis() {
  const s = ui.state;
  if (s?.draft) return `draft:${s.draft.updated_utc}`;
  return `candidate:${s?.proposal?.selected ?? null}`;
}
function inspBasisStale(d) {
  return Boolean(d && !d.error && d.basis && d.basis !== currentIssueBasis());
}

function issueStale(d, issue) {
  if (inspBasisStale(d) && issue.sheet !== undefined && issue.destination === "layer") {
    return { stale_reason: "the sheets changed; re-checking" };
  }
  const ids = ISSUE_SOURCES[issue.source] || [];
  return d.sources.find((s) => ids.includes(s.id) && s.stale) || null;
}

const issueNo = (i) => Number(String(i.id).replace(/\D/g, "")) || 0;

function sheetLabel(d, id) {
  const s = d.sheets.find((x) => x.id === id);
  return s ? `Layer ${s.layer_from_front} (from front) · ${s.color}` : id ? id : "whole stack";
}

const measure = (m) => (!m ? "–" : m.text ? esc(m.text) : `${esc(m.label)} <strong>${fmt(m.value, m.unit === "px" ? 0 : 3)}${m.unit === "px" ? " px" : ` ${esc(m.unit)}`}</strong>`);

// Which image an issue belongs on: its own layer; whole-stack checks on the front view; intake and
// planner findings on the artwork only.
function issueHome(i) {
  if (i.sheet) return i.sheet;
  return i.destination === "artwork" ? "artwork" : "front";
}

function inspImages(d) {
  const imgs = Object.fromEntries(d.images.map((i) => [i.key, i]));
  const out = [];
  for (const s of d.sheets.slice().reverse()) {  // front first
    if (imgs.front) out.push({ key: s.id, label: sheetLabel(d, s.id), short: `L${s.layer_from_front}`, color: s.color, url: s.image, extent_mm: imgs.front.extent_mm, layer: true });
  }
  if (imgs.front) out.push({ ...imgs.front, short: "Front view" });
  if (imgs.artwork) out.push({ ...imgs.artwork, short: "Artwork" });
  return out;
}

// The image shown: chosen in the strip, or (auto) the selected issue's home, else the first layer
// with an issue, else the front layer (the sheets are the focus), else the artwork.
function inspImage(d, issue) {
  const all = inspImages(d);
  const byKey = (k) => all.find((x) => x.key === k);
  if (ui.insp.view !== "auto" && byKey(ui.insp.view)) return byKey(ui.insp.view);
  if (issue && byKey(issueHome(issue))) return byKey(issueHome(issue));
  const first = d.issues.find((i) => i.sheet && byKey(i.sheet));
  return (first && byKey(first.sheet)) || all.find((x) => x.layer) || byKey("artwork") || all[0];
}

function filteredIssues(d) {
  const f = ui.insp;
  const key = ui.insp.shown?.key;
  return d.issues.filter((i) => (!f.kind || i.kind === f.kind) && (!f.severity || i.severity === f.severity)
    && (!f.status || (i.review?.state || "needs_review") === f.status) && (!key || issueHome(i) === key));
}

const layerShort = (d, id) => { const s = d.sheets.find((x) => x.id === id); return s ? `L${s.layer_from_front}` : id; };

// Hatch for pixels revealed through the selected opening: black and white stripes stay visible on any color,
// with gaps that keep the revealed color itself visible.
let hatch = null;
function hatchPattern(ctx) {
  if (!hatch) {
    hatch = document.createElement("canvas");
    hatch.width = hatch.height = 8;
    const h = hatch.getContext("2d");
    h.lineWidth = 1.25;  // thin black and white stripes; the revealed color shows between them
    for (const [color, off] of [["rgba(0,0,0,0.8)", 0], ["rgba(255,255,255,0.85)", 2]]) {
      h.strokeStyle = color;
      h.beginPath();
      for (const k of [-8, 0, 8]) { h.moveTo(k + off, 8); h.lineTo(k + off + 8, 0); }
      h.stroke();
    }
  }
  return ctx.createPattern(hatch, "repeat");
}

// Card parts: cause, what the issue reveals in the image, the same place on other layers, review state.
function inspCauseHtml(d, issue) {
  const f = issue.feature;
  if (!f) return "";
  const tag = f.cause === "image" ? "Image detail" : "Layer planner";
  const tip = f.cause === "image"
    ? "The processed image itself is narrower than the threshold here: every layer in front of this color needs an opening this small. Changing the image is the usual fix."
    : "The image is wide enough here (or nothing of it shows through): the narrow shape comes from how the planner split the image into layers.";
  let reveals = "";
  if (f.regions?.length) {
    reveals = f.regions.map((r) => `<span class="insp-reveal" title="${esc(`Image region ${r.color}: ${r.width_at_least ? "at least" : "about"} ${fmt(r.width_mm, 2)} mm wide at this place (±${fmt(f.width_precision_mm, 2)} mm, one pixel)${r.shown_by.length ? `; shown by ${r.shown_by.map((id) => layerShort(d, id)).join(", ")}` : ""}`)}">
      <span class="swatch" style="background:${esc(r.color)}"></span><span class="mono">${esc(r.color)}</span>
      <span class="muted">${r.width_at_least ? "≥" : "≈"} ${fmt(r.width_mm, 2)} mm${r.shown_by.length ? ` · ${r.shown_by.map((id) => layerShort(d, id)).join(", ")}` : ""}</span></span>`).join(" ");
  } else if (f.role) {
    reveals = `<span class="muted">nothing visible${f.covered_by?.length ? ` (under ${f.covered_by.map((id) => layerShort(d, id)).join(", ")})` : ""}</span>`;
  }
  return `<div class="insp-cause ${esc(f.cause)}" data-testid="insp-cause" data-cause="${esc(f.cause)}">
      <span class="cause-tag tip" tabindex="0" data-tip="${esc(tip)}">${tag}</span> <span class="small">${esc(f.cause_text)}</span>
    </div>
    ${reveals ? `<dl class="kv small"><dt>${f.role === "material" ? "Shows" : "Reveals"}</dt><dd data-testid="insp-reveals">${reveals}</dd></dl>` : ""}`;
}

function inspGroupMates(d, issue) {
  const group = issue.group && d.groups?.find((g) => g.id === issue.group);
  if (!group) return [];
  const front = (i) => d.sheets.find((s) => s.id === i.sheet)?.layer_from_front ?? 99;
  return group.members.filter((m) => m !== issue.id).map((m) => d.issues.find((i) => i.id === m)).filter(Boolean)
    .sort((a, b) => front(a) - front(b));
}

function inspReviewHtml(d, issue, mates) {
  const rv = issue.review || { state: "needs_review" };
  const buttons = REVIEW_STATES.map(([s, label, tip]) => `<button class="tool-btn tip ${rv.state === s ? "active" : ""}" aria-pressed="${rv.state === s}"
      data-action="insp-review" data-state="${s}" data-testid="insp-review-${s}" data-tip="${esc(tip)}" ${busy() ? "disabled" : ""}>${label}</button>`).join("");
  const differ = mates.filter((m) => (m.review?.state || "needs_review") !== rv.state);
  return `<div class="insp-review-row">
      <div class="segmented insp-review" role="group" aria-label="Review decision" data-testid="insp-review" data-state="${esc(rv.state)}">${buttons}</div>
      ${differ.length ? `<button class="link small" data-action="insp-review" data-state="${esc(rv.state)}" data-scope="group" data-testid="insp-review-group" ${busy() ? "disabled" : ""}>Same for all ${mates.length + 1} layers here</button>` : ""}
    </div>
    ${rv.state === "accept_risk" ? '<p class="small muted" style="margin:0" data-testid="insp-accepted">Risk accepted. The geometry and the warning are unchanged.</p>' : ""}
    ${rv.lapsed ? `<p class="small warn-text" style="margin:0" data-testid="insp-lapsed">Earlier decision “${esc(REVIEW_LABEL[rv.lapsed.state] || rv.lapsed.state)}” no longer applies: ${esc(rv.lapsed.reason)}. Please review again.</p>` : ""}`;
}

function inspTags(d, i) {
  const st = i.review?.state;
  const n = i.group ? (d.groups?.find((g) => g.id === i.group)?.sheets.length || 1) : 1;
  return [st === "accept_risk" ? '<span class="insp-tag accept" title="Risk accepted (warning kept)">accepted</span>' : "",
    st === "fix_later" ? '<span class="insp-tag later" title="Marked to fix later">later</span>' : "",
    i.review?.lapsed ? '<span class="insp-tag lapsed" title="An earlier decision no longer applies">review again</span>' : "",
    n > 1 ? `<span class="insp-tag group" title="The same place has an issue on ${n} layers">×${n}</span>` : ""].join("");
}

function checkedText(d, shown) {
  const c = d.checks;
  if (shown?.key === "artwork") return "No located artwork or intake findings.";
  if (!c) return "No sheets to check.";
  if (shown?.key === "front") return "The front view matches the artwork (no whole-stack issue). Each layer's own issues are on its tab.";
  return `No geometric issues found on this layer. Checked: every piece reaches the perimeter, connections at least ${fmt(c.structural_width_mm, 2)} mm,
    openings at least ${fmt(c.min_opening_mm, 2)} mm (unsupported below ${fmt(c.nozzle_diameter_mm, 2)} mm), material at least
    ${fmt(c.min_positive_feature_mm, 2)} mm (unsupported below ${fmt(c.line_width_mm, 2)} mm) — ${esc(c.geometry_source)}.`;
}

function issuesPanel() {
  if (!ui.state?.proposal) return "";
  const d = ui.issues;
  if ((!d || ui.issuesDirty) && !ui.issuesLoading && !busy()) queueMicrotask(loadIssues);  // the previous result stays meanwhile
  if (!d) {
    return `<section class="panel tight small muted" data-testid="issue-inspector">${busy() ? "Issues are shown when the running job has finished." : "Checking the sheets for issues…"}</section>`;
  }
  if (d.error) return `<section class="panel tight small no" data-testid="issue-inspector">Issues unavailable: ${esc(d.error)}</section>`;
  const selected = d.issues.find((i) => i.id === ui.insp.selected) || null;
  const shown = inspImage(d, selected);
  ui.insp.shown = shown;
  const list = filteredIssues(d);
  const issue = list.find((i) => i.id === ui.insp.selected) || null;
  const basisStale = inspBasisStale(d);
  const sheetIssues = d.issues.filter((i) => i.destination === "layer" || i.sheet);
  const artIssues = d.issues.filter((i) => issueHome(i) === "artwork");
  const countBadges = (items) => SEV_ORDER.map((s) => [s, items.filter((i) => i.severity === s).length]).filter(([, n]) => n)
    .map(([s, n]) => badge(SEV_CLASS[s] || "neutral", `${n} ${s.toLowerCase()}`)).join(" ");
  const stale = d.sources.filter((s) => s.stale);
  const options = (values, current) => values.map(([v, l]) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(l)}</option>`).join("");
  const here = d.issues.filter((i) => issueHome(i) === shown?.key);
  const kinds = [...new Set(here.map((i) => i.kind))].map((k) => [k, here.find((i) => i.kind === k).title]);
  const sevs = SEV_ORDER.filter((s) => here.some((i) => i.severity === s)).map((s) => [s, s]);
  const statuses = REVIEW_STATES.map(([s, l]) => [s, `${l} (${here.filter((i) => (i.review?.state || "needs_review") === s).length})`]);
  const dual = Boolean(shown && shown.key !== "artwork" && d.images.some((i) => i.key === "artwork"));
  const index = issue ? list.indexOf(issue) : -1;
  const hasDraft = Boolean(ui.state.draft && d.has_review);
  const chips = inspImages(d).map((img) => {
    const mine = d.issues.filter((i) => issueHome(i) === img.key);
    const worst = SEV_ORDER.find((s) => mine.some((i) => i.severity === s));
    return `<button class="insp-chip ${img.key === shown?.key ? "active" : ""}" data-action="insp-layer" data-key="${esc(img.key)}" data-testid="insp-layer-chip" title="${esc(img.label)}">
      ${img.layer ? `<span class="swatch" style="background:${esc(img.color)}"></span>` : ""}${esc(img.short)}
      ${mine.length ? `<span class="insp-count" style="background:${SEV_COLOR[worst] || "#888"}">${mine.length}</span>` : '<span class="insp-ok" aria-label="no issues">✓</span>'}</button>`;
  }).join("");
  const card = issue ? (() => {
    const st = issueStale(d, issue);
    const th = issue.threshold;
    const thText = !th ? "–" : th.text ? esc(th.text) : `${esc(th.label)} <strong>${fmt(th.value, 3)} ${esc(th.unit)}</strong>${th.nominal_mm != null ? ` <span class="muted">(nominal ${fmt(th.nominal_mm, 2)} mm)</span>` : ""}${th.unsupported_below_mm != null ? ` <span class="muted">(unsupported below ${fmt(th.unsupported_below_mm, 3)} mm)</span>` : ""}`;
    const fromCandidate = issue.destination === "layer" && !hasDraft && issue.sheet && d.sheet_source === "candidate";
    const layerBtn = issue.destination === "layer"
      ? `<button class="primary" data-action="insp-edit-layer" data-testid="insp-edit-layer" ${busy() || (!hasDraft && !fromCandidate) ? "disabled" : ""}>Show on layer</button>` : "";
    const artBtn = issue.artwork_box_px ? `<button class="${issue.destination === "artwork" ? "primary" : ""}" data-action="insp-edit-artwork" data-testid="insp-edit-artwork" ${busy() ? "disabled" : ""}>Edit artwork</button>` : "";
    const crop = issue.details?.image_url ? `<img class="insp-crop" src="${esc(issue.details.image_url)}" alt="original | default intake | as planned" data-testid="insp-detail-image">
      <p class="small muted" style="margin:0">original · default intake · as planned</p>` : "";
    const mates = inspGroupMates(d, issue);
    const mateLayers = new Set(mates.map((m) => m.sheet)).size;
    const group = mates.length ? `<dt>Same place</dt><dd data-testid="insp-group"><span class="muted">${mateLayers} other layer${mateLayers === 1 ? "" : "s"}:</span>
        ${mates.map((m) => `<button class="insp-mate tip" data-action="insp-goto" data-issue="${esc(m.id)}" data-testid="insp-mate" data-tip="${esc(`${m.title} on ${sheetLabel(d, m.sheet)}: ${m.measured?.value != null ? `${fmt(m.measured.value, 3)} ${m.measured.unit}` : "–"}`)}">
          <span class="dot" style="background:${SEV_COLOR[m.severity] || "#888"}"></span>${esc(layerShort(d, m.sheet))}${m.measured?.value != null ? ` <span class="muted">${fmt(m.measured.value, 2)}</span>` : ""}</button>`).join("")}</dd>` : "";
    return `<div class="subpanel insp-card" data-testid="insp-card" data-key="${esc(issue.key || "")}">
      <div class="spread"><strong><span class="insp-no">${issueNo(issue)}</span> <span data-testid="insp-title">${esc(issue.title)}</span></strong>${badge(SEV_CLASS[issue.severity] || "neutral", issue.severity)}</div>
      <dl class="kv small">
        <dt>Measured</dt><dd data-testid="insp-measured">${measure(issue.measured)}</dd>
        <dt>Threshold</dt><dd data-testid="insp-threshold">${thText}</dd>
        <dt>Where</dt><dd data-testid="insp-sheet">${issue.sheet ? `<span class="swatch" style="background:${esc(d.sheets.find((s) => s.id === issue.sheet)?.color || "#888")}"></span> ` : ""}${esc(issue.sheet ? sheetLabel(d, issue.sheet) : issue.destination === "artwork" ? "Artwork (flat color) — not a sheet defect" : "Whole stack (front view)")}${issue.outline_exact ? "" : ' <span class="muted">(bounding box only)</span>'}</dd>
        ${group}
      </dl>
      ${inspCauseHtml(d, issue)}
      ${inspReviewHtml(d, issue, mates)}
      ${crop}
      ${st ? `<p class="small warn-text" data-testid="insp-stale" style="margin:0">Out of date: ${esc(st.stale_reason || "re-check")}</p>` : ""}
      <div class="row">${layerBtn}${artBtn}</div>
      ${fromCandidate ? '<p class="small muted" style="margin:0">Show on layer starts the review of this candidate.</p>' : ""}
      ${issue.destination === "layer" && issue.artwork_box_px ? '<p class="small muted" style="margin:0">A sheet problem: changing the artwork may or may not remove it.</p>' : ""}
      <details class="small"><summary>Technical details</summary>
        <dl class="kv"><dt>Code</dt><dd><code>${esc(issue.kind)}</code></dd><dt>Source</dt><dd>${esc(issue.source)}</dd>
          <dt>Box (mm)</dt><dd class="mono">${esc((issue.bbox_mm || []).join(", "))}</dd>
          ${issue.bbox_mm ? (() => { const g = d.grid; const band = g.perimeter_pixels * g.mm_per_pixel; const top = g.outer_sheet_mm - band;
            return `<dt>In the artwork</dt><dd class="mono" data-testid="insp-aperture-pos">${fmt(issue.bbox_mm[0] - band, 2)} mm from the left, ${fmt(top - issue.bbox_mm[3], 2)} mm from the top of the ${fmt(g.artwork_pixels * g.mm_per_pixel, 1)} mm aperture</dd>`; })() : ""}
          ${issue.key ? `<dt>Key</dt><dd class="mono">${esc(issue.key)}</dd>` : ""}</dl>
        <pre class="mono small">${esc(JSON.stringify({ measured: issue.measured, threshold: issue.threshold, details: { ...issue.details, held_material_outline_mm: undefined } }, null, 2))}</pre></details>
    </div>`;
  })() : list.length ? `<div class="small muted" data-testid="insp-card-empty">Click a highlighted area or a row.</div>`
    : `<div class="subpanel small ${here.length ? "muted" : "ok-text"}" data-testid="insp-card-empty">${here.length ? "No issue here matches the filters." : checkedText(d, shown)}</div>`;
  const rows = list.map((i) => `<li class="${i.id === ui.insp.selected ? "active" : ""} ${issueStale(d, i) ? "stale" : ""}"><button class="insp-row" data-action="insp-select" data-issue="${esc(i.id)}" data-testid="insp-row">
      <span class="insp-no" style="background:${SEV_COLOR[i.severity] || "#888"}">${issueNo(i)}</span><span class="insp-row-title">${esc(i.title)}</span>
      ${inspTags(d, i)}<span class="small muted">${i.measured && i.measured.value != null ? `${fmt(i.measured.value, i.measured.unit === "px" ? 0 : 2)} ${esc(i.measured.unit)}` : ""}</span></button></li>`).join("");
  const rc = d.review?.counts || {};
  const decided = [rc.accept_risk ? `${rc.accept_risk} accepted` : "", rc.fix_later ? `${rc.fix_later} to fix later` : ""].filter(Boolean).join(", ");
  const summary = [sheetIssues.length ? `<span class="small muted">sheets</span> ${countBadges(sheetIssues)}` : '<span class="small ok-text">sheets: no issues</span>',
    artIssues.length ? `<span class="small muted">artwork</span> ${countBadges(artIssues)}` : "",
    decided ? `<span class="small muted" data-testid="insp-decided">· ${esc(decided)}</span>` : ""].filter(Boolean).join(" ");
  const reviewNotes = [d.review?.lapsed ? `${d.review.lapsed} earlier decision${d.review.lapsed === 1 ? " no longer applies" : "s no longer apply"} because the image, the layers or the print settings changed there (marked “review again”).` : "",
    d.review?.unmatched ? `${d.review.unmatched} earlier decision${d.review.unmatched === 1 ? " matches" : "s match"} no current issue (the issue is gone or changed).` : "",
    d.review?.artwork_edited && (rc.accept_risk || rc.fix_later) ? "The artwork was edited after planning: decisions refer to the planned image until you plan again." : ""].filter(Boolean);
  return `<details class="panel section insp" data-section="issues" data-testid="issue-inspector" ${openAttr("issues", true)}>
    <summary><h3>Issues</h3><span class="small" data-testid="insp-summary">${summary}${stale.length || basisStale ? " " + badge("warn", "out of date") : ""}</span></summary>
    <div class="section-body stack">
      ${d.notice ? `<div class="banner info small" data-testid="insp-notice">${esc(d.notice)}</div>` : ""}
      ${basisStale ? `<div class="banner warn small" data-testid="insp-basis-stale">The sheets changed: the locations shown are from before the edit. ${ui.issuesLoading ? "Re-checking…" : "They are checked again shortly."}</div>` : ""}
      ${stale.map((s) => `<div class="banner warn small" data-testid="insp-source-stale"><strong>${esc(s.label)}</strong> is out of date: ${esc(s.stale_reason || "re-check")}.</div>`).join("")}
      ${reviewNotes.length ? `<div class="banner info small" data-testid="insp-review-notes">${reviewNotes.map(esc).join("<br>")}</div>` : ""}
      <div class="insp-chips" role="tablist" aria-label="Layer" data-testid="insp-layers">${chips}</div>
      <div class="insp-grid ${dual ? "dual" : ""}">
        <div class="insp-main">
        <div class="insp-views">
        ${dual ? `<figure class="insp-view insp-art" data-testid="insp-art-view">
          <figcaption class="small"><strong>Processed image</strong> <span class="muted">as planned</span>
            ${infoTip("The flat-color image from the intake that the layers were planned from (not the uploaded original). The grey band is the sheet border, not part of the image. Zoom and pan follow the layer view.")}</figcaption>
          <div class="zoom-viewport insp-viewport ${ui.artSpace ? "space" : ""}" id="insp-art-viewport" data-testid="insp-art-viewport" tabindex="0" aria-label="Processed image (mouse wheel zooms; + / - / 0 with focus)">
            <div class="zoom-stage" id="insp-art-stage"><img id="insp-art-image" src="${esc(paddedArtwork(d))}" alt="Processed image (as planned)" draggable="false"></div>
            <canvas id="insp-art-overlay" data-testid="insp-art-canvas" aria-label="Issue locations on the processed image"></canvas>
          </div>
        </figure>` : ""}
        <figure class="insp-view insp-layer">
          <figcaption class="small"><strong data-testid="insp-shown">${esc(shown?.label || "")}</strong>${shown?.layer ? ` <span class="muted">material in color, cutouts grey</span>` : ""}</figcaption>
          <div class="zoom-viewport insp-viewport ${ui.artSpace ? "space" : ""}" id="insp-viewport" data-testid="insp-viewport" tabindex="0" aria-label="Issues (mouse wheel zooms; + / - / 0 with focus)">
            <div class="zoom-stage" id="insp-stage"><img id="insp-image" src="${esc(shown?.url || "")}" alt="${esc(shown?.label || "")}" draggable="false"></div>
            <canvas id="insp-overlay" data-testid="insp-canvas" aria-label="Issue locations"></canvas>
          </div>
        </figure>
        </div>
        <div class="spread insp-viewbar">${dual ? `<span class="small muted insp-legend">
            <span><i class="lg-issue"></i>issue</span><span><i class="lg-revealed"></i>revealed through it</span><span><i class="lg-region"></i>image region</span></span>` : "<span></span>"}${zoomBar("insp")}</div>
        </div>
        <div class="stack insp-side" style="gap:8px">
          ${here.length ? `<div class="row insp-filters">
            <select id="insp-kind" data-testid="insp-filter-kind" aria-label="Type"><option value="">All types</option>${options(kinds, ui.insp.kind)}</select>
            <select id="insp-severity" data-testid="insp-filter-severity" aria-label="Severity"><option value="">All severities</option>${options(sevs, ui.insp.severity)}</select>
            <select id="insp-status" data-testid="insp-filter-status" aria-label="Review status"><option value="">All statuses</option>${options(statuses, ui.insp.status)}</select>
          </div>
          <div class="row"><button class="tool-btn" data-action="insp-step" data-step="-1" data-testid="insp-prev" ${list.length ? "" : "disabled"}>◀</button>
            <span class="small" data-testid="insp-position">${index >= 0 ? index + 1 : "–"} / ${list.length}</span>
            <button class="tool-btn" data-action="insp-step" data-step="1" data-testid="insp-next" ${list.length ? "" : "disabled"}>▶</button></div>` : ""}
          ${card}
          ${list.length ? `<ul class="insp-list" data-testid="insp-list">${rows}</ul>` : ""}
        </div>
      </div>
      ${d.unlocalized.length ? `<details class="small" data-testid="insp-unlocalized"><summary>${d.unlocalized.length} other diagnostic${d.unlocalized.length === 1 ? "" : "s"} without a location (not drawn)</summary>
        <ul>${d.unlocalized.map((u) => `<li>${badge(SEV_CLASS[u.severity] || "neutral", u.severity)} <code>${esc(u.code)}</code>${u.sheet ? ` (${esc(u.sheet)})` : u.source === "intake" || u.source === "proposal" ? " (artwork / intake)" : ""} — ${esc(u.text)}</li>`).join("")}</ul></details>` : ""}
    </div>
  </details>`;
}

// Image pixels of a sheet-mm point on the shown image (its extent in mm).
function inspToImage(entry, img, [x, y]) {
  const [x0, y0, x1, y1] = entry.extent_mm;
  return [((x - x0) / (x1 - x0)) * img.naturalWidth, ((y1 - y) / (y1 - y0)) * img.naturalHeight];
}

function inspOnImage(d, i) {
  return Boolean(ui.insp.shown && i.outline_mm && issueHome(i) === ui.insp.shown.key);
}

// Screen geometry of the issues drawn on one of the inspector images (shared by drawing and hit
// testing): the layer / front view ("layer") or the processed image beside it ("art"). The padded
// processed image spans the same sheet extent as the layer images, so one mm-to-image map serves both.
const INSP_SIDES = { layer: ["#insp-image", "#insp-overlay"], art: ["#insp-art-image", "#insp-art-overlay"] };
function inspScreen(side = "layer") {
  const [imgSel, canvasSel] = INSP_SIDES[side];
  const img = $(imgSel);
  const canvas = $(canvasSel);
  const shown = ui.insp.shown;
  const entry = side === "art" ? { extent_mm: [0, 0, ui.issues.grid.outer_sheet_mm, ui.issues.grid.outer_sheet_mm] } : shown;
  if (!img || !canvas || !img.naturalWidth || !entry || !img.getAttribute("src")) return null;
  const c = canvas.getBoundingClientRect();
  const r = img.getBoundingClientRect();
  const at = (p) => { const [px, py] = inspToImage(entry, img, p); return [r.left - c.left + (px * r.width) / img.naturalWidth, r.top - c.top + (py * r.height) / img.naturalHeight]; };
  return { img, canvas, c, r, at, side };
}

const MARKER_R = 9;
function inspMarker(g, issue) {  // numbered marker beside the issue (above-right), never on top of it
  const [a, b] = [g.at([issue.bbox_mm[0], issue.bbox_mm[3]]), g.at([issue.bbox_mm[2], issue.bbox_mm[1]])];
  const small = Math.max(b[0] - a[0], b[1] - a[1]) < 14;
  const ring = small ? Math.max(12, Math.hypot(b[0] - a[0], b[1] - a[1]) / 2 + 6) : 0;
  const cx = (a[0] + b[0]) / 2, cy = (a[1] + b[1]) / 2;
  const pos = small ? [cx + ring * 0.71 + MARKER_R * 0.7, cy - ring * 0.71 - MARKER_R * 0.7] : [b[0] + MARKER_R + 2, a[1] - MARKER_R - 2];
  return { a, b, cx, cy, small, ring, pos };
}

function drawInspOverlay() {
  const d = ui.issues;
  if (!d || d.error) return;
  for (const side of ["layer", "art"]) drawInspSide(d, side);
}

function drawInspSide(d, side) {
  const g = inspScreen(side);
  if (!g) return;
  const { canvas, c } = g;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(c.width * dpr));
  const h = Math.max(1, Math.round(c.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, c.width, c.height);
  const trace = (outline) => {
    ctx.beginPath();
    for (const poly of outline) for (const ring of poly) ring.forEach((p, k) => ctx[k ? "lineTo" : "moveTo"](...g.at(p)));
  };
  const list = filteredIssues(d).filter((i) => inspOnImage(d, i));
  const sel = list.find((i) => i.id === ui.insp.selected);
  const art = side === "art";
  // Outlines only (no fill on the image) so small holes and thin features stay visible: a dark halo, then the color.
  const outline = (issue, width, selected) => {
    const color = SEV_COLOR[issue.severity] || "#888";
    const stale = Boolean(issueStale(d, issue));
    ctx.save();
    if (stale) ctx.setLineDash([4, 3]);
    trace(issue.outline_mm);
    if (selected && !art) { ctx.globalAlpha = 0.22; ctx.fillStyle = color; ctx.fill("evenodd"); ctx.globalAlpha = 1; }
    ctx.lineJoin = "round";
    ctx.strokeStyle = selected ? "#fff" : "rgba(0,0,0,0.75)";
    ctx.lineWidth = width + 2.5;
    ctx.stroke();
    ctx.strokeStyle = stale ? "#999" : color;
    ctx.lineWidth = width;
    ctx.stroke();
    ctx.restore();
  };
  for (const issue of list) if (issue !== sel) outline(issue, art ? 1 : 1.5, false);
  if (sel) {
    if (sel.details?.held_material_outline_mm) {  // the piece a narrow connection holds: thin dashed
      ctx.save(); ctx.setLineDash([5, 4]); ctx.strokeStyle = SEV_COLOR[sel.severity]; ctx.lineWidth = 1;
      trace(sel.details.held_material_outline_mm); ctx.stroke(); ctx.restore();
    }
    outline(sel, 2.5, true);
  }
  // On the processed image, on top: what the selected issue reveals (hatched) and the image region it
  // belongs to (black + white dashed outline), so the responsible detail is visible on any color. A large
  // region is not outlined (only its part near the issue was measured); its revealed pixels are hatched.
  if (art && sel?.feature) {
    const f = sel.feature;
    for (const r of f.regions || []) {
      if (r.outline_clipped) continue;
      ctx.save(); trace(r.outline_mm); ctx.lineJoin = "round";
      ctx.strokeStyle = "rgba(0,0,0,0.85)"; ctx.lineWidth = 3.5; ctx.stroke();
      ctx.setLineDash([5, 4]); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke(); ctx.restore();
    }
    if (f.revealed_outline_mm) {
      ctx.save(); trace(f.revealed_outline_mm); ctx.fillStyle = hatchPattern(ctx); ctx.fill("evenodd");
      ctx.strokeStyle = "rgba(0,0,0,0.9)"; ctx.lineWidth = 1; ctx.stroke(); ctx.restore();
    }
  }
  for (const issue of list) {  // rings around features too small to see, and the numbered markers
    const m = inspMarker(g, issue);
    const color = issueStale(d, issue) ? "#999" : SEV_COLOR[issue.severity] || "#888";
    ctx.save();
    if (m.small) {
      ctx.setLineDash(issue === sel ? [6, 4] : [3, 3]);
      ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(m.cx, m.cy, m.ring, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = color; ctx.lineWidth = issue === sel ? 2 : 1.5; ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(m.pos[0], m.pos[1], issue === sel ? MARKER_R + 2 : MARKER_R, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = issue === sel ? 2.5 : 1.5; ctx.stroke();
    ctx.fillStyle = issue.severity === "WARN" ? "#000" : "#fff";
    ctx.font = `bold ${issue === sel ? 11 : 10}px system-ui, sans-serif`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(issueNo(issue)), m.pos[0], m.pos[1] + 0.5);
    ctx.restore();
  }
}

// Zoom the view so ``box`` (image px [x0, y0, x1, y1]) fills about ``fill`` of it and centre it. A
// focused view may pan past the image edge (``free``), so a feature near the border is centred too;
// Fit ends that.
function focusView(key, box, fill = 0.45) {
  const g = viewGeometry(key);
  if (!g) return;
  const bw = Math.max(box[2] - box[0], 1);
  const bh = Math.max(box[3] - box[1], 1);
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const zoom = Math.max(1.0001, Math.min(ZOOM_MAX, (fill * Math.min(g.vw / bw, g.vh / bh)) / g.fit));
  g.state.free = true;
  g.state.zoom = zoom;
  const scale = g.fit * zoom;
  g.state.pan = [g.vw / 2 - cx * scale, g.vh / 2 - cy * scale];
  applyView(key);
}

function afterImage(selector, fn) {
  const img = $(selector);
  if (!img) return;
  if (img.complete && img.naturalWidth) fn();
  else img.addEventListener("load", () => fn(), { once: true });
}

function focusInspIssue(issue) {
  afterImage("#insp-image", () => {
    const img = $("#insp-image");
    const entry = ui.insp.shown;
    if (!entry || !issue.bbox_mm) return;
    const [x0, y0] = inspToImage(entry, img, [issue.bbox_mm[0], issue.bbox_mm[3]]);
    const [x1, y1] = inspToImage(entry, img, [issue.bbox_mm[2], issue.bbox_mm[1]]);
    focusView("insp", [x0, y0, x1, y1]);
  });
}

function selectIssue(id) {
  const issue = ui.issues?.issues?.find((i) => i.id === id);
  ui.insp.selected = id;
  ui.insp.selectedKey = issue?.key || null;
  if (issue && ui.insp.view !== "auto" && issueHome(issue) !== ui.insp.view) ui.insp.view = issueHome(issue);
  if (issue && ui.insp.status && (issue.review?.state || "needs_review") !== ui.insp.status) ui.insp.status = "";  // keep it listed
  render();
  if (issue) focusInspIssue(issue);
}

// Another layer in the strip: the view keeps its zoom and position (all layer images and the processed
// image share the sheet extent), so the same place stays in view; Fit resets it.
function inspShowLayer(key) {
  const d = ui.issues;
  if (!d || d.error) return;
  ui.insp.view = key;
  ui.insp.kind = "";
  ui.insp.severity = "";
  const sel = d.issues.find((i) => i.id === ui.insp.selected);
  if (sel && issueHome(sel) !== key) { ui.insp.selected = null; ui.insp.selectedKey = null; }
  render();
}

function bindInspHits(canvas, side) {
  let down = null;
  canvas.addEventListener("pointerdown", (e) => { if (e.button === 0 && !ui.artSpace) down = [e.clientX, e.clientY]; });
  canvas.addEventListener("pointerup", (e) => {
    if (!down || e.button !== 0) return;
    const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]);
    down = null;
    const d = ui.issues;
    const g = d && !d.error ? inspScreen(side) : null;
    if (moved > 4 || !g) return;
    const x = e.clientX - g.c.left, y = e.clientY - g.c.top;
    const tol = 6;
    const hits = filteredIssues(d).filter((i) => inspOnImage(d, i) && i.bbox_mm).map((i) => {
      const m = inspMarker(g, i);
      const onMarker = Math.hypot(x - m.pos[0], y - m.pos[1]) <= MARKER_R + 2;
      const inBox = x >= m.a[0] - tol && x <= m.b[0] + tol && y >= m.a[1] - tol && y <= m.b[1] + tol;
      const inRing = m.small && Math.hypot(x - m.cx, y - m.cy) <= m.ring;
      return { i, onMarker, hit: onMarker || inBox || inRing, area: (m.b[0] - m.a[0]) * (m.b[1] - m.a[1]) };
    }).filter((h) => h.hit);
    if (!hits.length) return;
    hits.sort((p, q) => (q.onMarker - p.onMarker) || p.area - q.area);
    selectIssue(hits[0].i.id);
  });
}

function bindInspector() {
  const canvas = $("#insp-overlay");
  if (!canvas) return;
  bindViewport("insp", canvas);
  $("#insp-image")?.addEventListener("load", () => applyView("insp"));
  bindInspHits(canvas, "layer");
  const artCanvas = $("#insp-art-overlay");
  if (artCanvas) {
    bindViewport("inspa", artCanvas);
    $("#insp-art-image")?.addEventListener("load", () => { applyView("insp"); syncView("insp", "inspa"); });
    bindInspHits(artCanvas, "art");
  }
  applyView("insp");
}

// Open the sheet editor on the issue: its layer selected, a marker on the location, zoomed onto it.
// Issues of a proposed candidate (no review yet) start its review first, then open the editor.
function inspEditLayer(issue) {
  if (!issue) return;
  if (!ui.state.draft) {
    const candidate = ui.state.proposal?.selected;
    if (!candidate || busy()) return;
    ui.inspPendingEdit = { sheet: issue.sheet, at_mm: issue.at_mm, bbox_mm: issue.bbox_mm };
    submit(`/api/projects/${encodeURIComponent(ui.name)}/review`, { candidate });
    return;
  }
  if (issue.sheet) ui.sheet = issue.sheet;
  ui.marker = issue.at_mm;
  ui.pending = null;
  ui.view = issue.sheet ? "sheet" : "front";
  setTab("review");
  render();
  $("#edit-viewport")?.scrollIntoView({ block: "center" });
  const outer = ui.state.draft.validation.outer_sheet_mm;
  afterImage("#edit-image", () => {
    const img = $("#edit-image");
    const b = issue.bbox_mm;
    const px = (x) => (x / outer) * img.naturalWidth;
    const py = (y) => (1 - y / outer) * img.naturalHeight;
    focusView("edit", [px(b[0]), py(b[3]), px(b[2]), py(b[1])]);
  });
}

// Open the Artwork editor (in Review layers while a review exists, else on the Plan page) zoomed onto the issue's pixels.
function inspEditArtwork(issue) {
  const box = issue?.artwork_box_px;
  if (!box) return;
  ui.sections.artwork = true;
  ui.art.focus = box;
  setTab(ui.state.draft ? "review" : "proposal");
  render();
  $('[data-testid="artwork-panel"]')?.scrollIntoView({ block: "start" });
  afterImage("#art-image", () => focusView("art", [box[1], box[0], box[3], box[2]]));
}

// ------------------------------------------------------------------------------ review
function selectedSheet() {
  return ui.state.draft.sheets.find((s) => s.id === ui.sheet) || null;
}

function reviewView() {
  const sheet = selectedSheet();
  return `${reviewBar()}
  <div class="review" data-testid="review-layout">
    <aside class="review-side">${layersPanel()}${historyPanel()}</aside>
    <div class="review-main">
      ${draftBanner()}
      <div class="panel tight stage">${canvasPanel(sheet)}</div>
      ${issuesPanel()}
      ${artworkPanel()}
      ${validationPanel()}
      ${changePanel()}
      ${previewsPanel()}
    </div>
    <aside class="review-side">${sheet ? sheetPanel(sheet) : '<div class="panel tight muted small">Select a layer.</div>'}</aside>
  </div>`;
}

// Review Layers inspects the sheets (no sheet-geometry tools); the Artwork editor sits below the issues.
// Earlier layer edits can be undone.
function reviewBar() {
  const d = ui.state.draft;
  const ok = d.status.printable;
  const visibleChange = !ok && d.validation.failures.some((f) => /reconstruction differs/.test(f));
  const dis = busy() ? "disabled" : "";
  const v = ui.state.verify;
  const verified = v ? (v.replay_identical
    ? `<span class="small"><span class="yes">✓</span> Reloading and replaying all ${v.edits} edits reproduces the saved geometry exactly.</span>`
    : `<span class="small"><span class="no">✗</span> Replay differs: ${esc(v.differences.join(", "))}</span>`) : "";
  const exportOk = ok && !ui.state.artwork?.differs_from_planned && !unsavedArt();
  const why = unsavedArt() ? "Save or discard the unsaved artwork changes first" : ui.state.artwork?.differs_from_planned
    ? "The artwork was edited after these sheets were planned" : ok ? "Export STEP/STL for every layer" : "Invalid drafts cannot be exported";
  return `<div class="review-bar" data-testid="review-bar">
    <div class="review-bar-status">
      ${badge(ok ? "ok" : "bad", ok ? "Valid" : "Invalid")}
      <span class="small muted">${d.sheets.length} layers · ${fmt(d.total_height_mm)} mm stack · ${d.edits.length} edit${d.edits.length === 1 ? "" : "s"}</span>
    </div>
    <div class="review-bar-actions">
      ${visibleChange ? `<button class="tool-btn attention tip" data-action="propose-op" data-op='${esc(JSON.stringify({ op: "accept_artwork" }))}' ${dis}
        data-tip="Only for an intended visible change: the current front view becomes the reviewed artwork">Accept front view as artwork…</button>` : ""}
      ${d.edits.length ? `<button class="tool-btn tip" data-action="undo" ${dis} data-tip="Undo the last layer edit">${ICON.undo}<span>Undo last edit</span></button>` : ""}
      <span class="divider" aria-hidden="true"></span>
      <button class="tool-btn ghost" data-action="tab" data-tab="printability">Printability →</button>
      <button class="primary" data-action="tab" data-tab="kit" ${exportOk ? "" : "disabled"} title="${esc(why)}">Build print kit →</button>
    </div>
    ${verified ? `<div class="review-bar-note">${verified} <span class="muted small">(${esc(v.checked_utc)})</span></div>` : ""}
  </div>`;
}

function layersPanel() {
  const d = ui.state.draft;
  const frontToBack = [...d.sheets].reverse();
  const list = frontToBack.map((s, i) => {
    const fails = s.checks.pass === false;
    const selected = s.id === ui.sheet;
    return `
    <li class="layer ${selected ? "selected" : ""} ${fails ? "failing" : ""}" data-action="select-sheet" data-sheet="${esc(s.id)}" role="option" aria-selected="${selected}"
        tabindex="${selected || (!ui.sheet && !i) ? 0 : -1}" data-testid="layer-row" title="${esc(s.export_name)} · ${esc(s.color)}${fails ? " · fails a check" : ""}">
      <span class="layer-index">${i + 1}</span>
      ${swatch(s.color, true)}
      <div class="layer-text"><div class="name">${esc(s.export_name)}</div>
        <div class="meta">${fmt(s.thickness_mm)} mm${s.rim ? ` · rim ${fmt(s.rim.width_mm)}×${fmt(s.rim.total_height_mm)}` : ""} · ${s.cutout_holes} cutout${s.cutout_holes === 1 ? "" : "s"}${s.islands ? ` · <span class="no">${s.islands} island${s.islands === 1 ? "" : "s"}</span>` : ""}</div></div>
      ${fails ? '<span class="layer-flag" aria-label="fails a check">!</span>' : ""}
    </li>`;
  }).join("");
  return `<div class="panel tight layers-panel" data-testid="layers-panel">
    <div class="spread"><h3 style="margin:0">Layers</h3><span class="small muted">${d.sheets.length} · ${fmt(d.total_height_mm)} mm</span></div>
    <div class="end-label">Front</div>
    <ul class="layer-list" role="listbox" aria-label="Layers, front first">${list}</ul>
    <div class="end-label">Back</div>
  </div>`;
}

function draftBanner() {
  const d = ui.state.draft;
  if (d.stale) return `<div class="banner warn">The saved project changed outside this view. <button class="small" data-action="reload">Reload</button></div>`;
  return "";
}

function canvasPanel(sheet) {
  const d = ui.state.draft;
  const images = d.previews.images;
  const views = [["front", "Assembled"], ["sheet", "Layer"], ["changes_base", "Changes vs base"], ["changes_previous", "Last change"]]
    .filter(([v]) => v === "sheet" || (v === "front" && images.assembled_front) || (v === "changes_base" && images.changes_vs_base) || (v === "changes_previous" && images.changes_vs_previous));
  const caption = {
    front: "Assembled front view of all layers",
    sheet: sheet ? `Layer ${sheet.export_name} alone` : "No layer selected",
    changes_base: "Changes since the base proposal",
    changes_previous: "Changes made by the last action",
  }[ui.view] || "";
  const legend = ui.view.startsWith("changes")
    ? '<span class="legend"><span><i style="background:#ff0000"></i>visible change</span><span><i style="background:#285aff"></i>hidden change</span></span>'
    : ui.view === "sheet" ? '<span class="legend"><span><i style="background:#ebebeb;border:1px solid #bbb"></i>cutout</span></span>' : "";
  const title = ui.view === "sheet" && sheet ? `<span class="stage-title small">${swatch(sheet.color)} ${esc(sheet.export_name)}</span>` : "";
  return `
    <div class="stage-head">
      <div class="segmented" role="tablist" aria-label="View" data-testid="view-switch">${views.map(([v, l]) => `<button class="small ${ui.view === v ? "active" : ""}" data-action="view" data-view="${v}" role="tab" aria-selected="${ui.view === v}">${l}</button>`).join("")}</div>
      ${title}
      ${zoomBar("edit")}
    </div>
    ${d.previews.notice ? `<p class="small warn-text stage-note" data-testid="preview-notice">${esc(d.previews.notice)}</p>` : ""}
    <div class="zoom-viewport edit-viewport review-viewport ${ui.artSpace ? "space" : ""}" id="edit-viewport" data-testid="edit-viewport" tabindex="0"
      aria-label="${esc(caption)} (mouse wheel zooms, drag moves the zoomed view; + / - / 0 with focus)" data-marker="${esc(JSON.stringify(ui.marker || null))}">
      <div class="zoom-stage" id="edit-stage"><img id="edit-image" src="${esc(viewImage(sheet))}" alt="${esc(caption)}" draggable="false"></div>
      <canvas id="overlay" aria-label="${esc(caption)}"></canvas>
    </div>
    <div class="spread small stage-foot">
      <span class="coords muted" id="coords">${ui.hover ? `x ${ui.hover[0].toFixed(2)} mm · y ${ui.hover[1].toFixed(2)} mm` : "x, y (mm)"}</span>
      ${legend}
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

// The one edit still proposed from this screen: accepting an intended visible change (earlier edits).
function pendingPanel() {
  if (!ui.pending) return "";
  return `<div class="pending" data-testid="pending-edit">
    <div class="spread"><strong>${esc(describeOp(ui.pending))}</strong>
      <div class="row"><button class="small ghost" data-action="cancel-pending">Cancel</button><button class="small primary" data-action="apply-pending" ${busy() ? "disabled" : ""}>Apply</button></div></div>
  </div>`;
}

function describeOp(op) {
  return op.op === "accept_artwork" ? "Accept the current front view as the reviewed artwork" : op.op;
}

function validationPanel() {
  const v = ui.state.draft.validation;
  const status = ui.state.draft.status;
  const ok = status.printable;
  const hints = v.failures.map((f) => {
    let hint = "";
    if (/reconstruction differs/.test(f)) hint = "The front view no longer matches the reviewed artwork. Undo the edit, or accept the front view if the change is intended.";
    else if (/sheet checks failed/.test(f)) hint = "A sheet breaks a physical rule (one 4-connected piece, material at least 1.2 mm wide, complete perimeter). Sheets are not edited here: undo the earlier layer edits, or change the artwork or the plan settings and plan again. Not every sheet problem can be solved in the artwork.";
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
  const islands = ui.state.draft.islands.map((i) => `<li>${esc(i.sheet)}: ${fmt(i.area_mm2)} mm²${i.visible_px ? `, ${i.visible_px} px visible` : ", hidden"}
      <button class="small" data-action="locate" data-sheet="${esc(i.sheet)}" data-x="${i.point_on_island_mm[0]}" data-y="${i.point_on_island_mm[1]}">Locate</button></li>`).join("");
  const summary = ok
    ? `front view ${status.reconstruction_vs_reviewed_target_px === 0 ? "identical to the reviewed artwork" : `${fmt(status.reconstruction_vs_reviewed_target_px)} px differ`} · ${fmt(status.floating_islands)} floating islands`
    : `${v.failures.length} failure${v.failures.length === 1 ? "" : "s"} · not exportable`;
  // Problems keep the section open; a valid draft collapses to its one-line summary.
  return `<details class="panel tight section" data-testid="validation-panel" data-section="validation" ${openAttr("validation", false, !ok || Boolean(islands))}>
    <summary><h3>Validation ${badge(ok ? "ok" : "bad", ok ? "REVIEWED_VALID" : "DRAFT_INVALID")}</h3><span class="small muted">${esc(summary)}</span></summary>
    <div class="section-body">
      ${hints ? `<ul class="small tight-list">${hints}</ul>` : ""}
      ${islands ? `<h4 style="margin-top:10px">Floating islands</h4><ul class="small tight-list">${islands}</ul>` : ""}
      <dl class="kv small">
        <dt>Front view vs reviewed artwork</dt><dd>${status.reconstruction_vs_reviewed_target_px === 0 ? '<span class="yes">identical</span>' : `<span class="no">${fmt(status.reconstruction_vs_reviewed_target_px)} px differ</span>`}</dd>
        <dt>Deviation from source artwork</dt><dd>${fmt(status.deviation_from_source_artwork_px)} px</dd>
        <dt>Repeated colors</dt><dd>${(v.repeated_colors || []).map((h) => swatch(h)).join(" ") || "none"}</dd>
      </dl>
      <details data-section="per-sheet" ${openAttr("per-sheet", false)}><summary>Per-layer checks</summary>
        <div class="table-wrap"><table><thead><tr><th>Layer (front first)</th><th>Pass</th><th>4-connected</th><th>Width ≥ 1.2 mm</th><th>Perimeter</th><th>Visible ⊂ material</th><th>Islands</th><th>Exact geometry</th></tr></thead><tbody>${rows}</tbody></table></div>
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
  return `<details class="panel tight section" data-testid="change-panel" data-section="change" ${openAttr("change")}>
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
    <summary><h3>More previews</h3><span class="small muted">exploded stack · artwork comparison</span></summary>
    <div class="section-body">
      <div class="gallery">
        ${images.exploded ? `<figure><img class="img" src="${esc(images.exploded)}" alt="exploded" loading="lazy"><figcaption>Exploded stack (back to front)</figcaption></figure>` : ""}
      </div>
      ${images.source_comparison ? `<figure style="margin-top:12px"><img class="img" src="${esc(images.source_comparison)}" alt="comparison" loading="lazy"><figcaption>Reviewed artwork | assembled | mismatches in red · click to open full size</figcaption></figure>` : ""}
    </div>
  </details>`;
}

function sheetPanel(sheet) {
  const sheets = ui.state.draft.sheets;
  const index = sheets.findIndex((s) => s.id === sheet.id);
  const fromFront = sheets.length - 1 - index;
  const dis = busy() ? "disabled" : "";
  const fails = sheet.checks.pass === false;
  const rimKey = (s) => (s.rim ? `${s.rim.width_mm}x${s.rim.total_height_mm}` : "");
  const rimOnAll = Boolean(sheet.rim) && sheets.every((s) => rimKey(s) === rimKey(sheet));
  return `<div class="panel tight inspector" data-testid="sheet-panel">
    <div class="inspector-head">
      ${swatch(sheet.color, true)}
      <div><h3 style="margin:0">${esc(sheet.export_name)}</h3>
        <div class="small muted">Layer ${fromFront + 1} of ${sheets.length} · ${esc(sheet.color)}</div></div>
      ${fails ? badge("bad", "fails") : badge("ok", "ok")}
    </div>
    <div class="facts">
      <div><span>${fmt(sheet.thickness_mm)}</span>mm thick</div>
      <div><span>${sheet.cutout_holes}</span>cutouts</div>
      <div><span class="${sheet.islands ? "no" : ""}">${sheet.islands}</span>islands</div>
      <div><span>${fmt(sheet.retained_fraction * 100, 0)}%</span>material</div>
    </div>
    <details class="inspector-section" data-section="layer-settings" data-testid="layer-settings" ${openAttr("layer-settings")}>
      <summary><h4>Layer settings</h4><span class="small muted">color · thickness · order · rim</span></summary>
      <div class="field-row"><label for="p-color">Color</label><input type="color" id="p-color" value="${esc(sheet.color.toLowerCase())}"><button class="small" data-action="recolor" ${dis}>Recolor</button></div>
      <div class="field-row"><label for="p-thick">Thickness</label><span><input type="number" id="p-thick" value="${sheet.thickness_mm}" min="0.2" step="0.2"> mm</span><button class="small" data-action="thickness" ${dis}>Set</button></div>
      <div class="field-row"><label>Order</label><span class="row">
        <button class="small" data-action="move" data-to="${index + 1}" ${index >= sheets.length - 1 ? "disabled" : dis} title="Toward the front">↑ Front</button>
        <button class="small" data-action="move" data-to="${index - 1}" ${index <= 0 ? "disabled" : dis} title="Toward the back">↓ Back</button></span></div>
      <div class="settings-rim">
        <div class="row small"><label class="check"><input type="checkbox" id="p-rim" ${sheet.rim ? "checked" : ""}> Raised rim</label>
          <span class="muted">${sheet.rim ? `${fmt(sheet.rim.width_mm)} × ${fmt(sheet.rim.total_height_mm)} mm${rimOnAll ? " · all layers" : ""}` : "off"}</span>
          ${infoTip("Optional raised border on the shared 12.5 mm perimeter band. Off by default and never required. With all layers the setting is one edit (one undo step).")}</div>
        <label class="check small"><input type="checkbox" id="p-rim-all" data-testid="rim-all" ${ui.rimAll ? "checked" : ""}> All ${sheets.length} layers</label>
        <div class="field-row"><label for="p-rim-w">Width</label><span><input type="number" id="p-rim-w" value="${sheet.rim ? sheet.rim.width_mm : 6}" min="0.5" max="12.5" step="0.5"> mm</span></div>
        <div class="field-row"><label for="p-rim-h">Height</label><span><input type="number" id="p-rim-h" value="${sheet.rim ? sheet.rim.total_height_mm : r3(Math.max(...sheets.map((s) => s.thickness_mm)) + 0.8)}" min="0.2" step="0.2"> mm</span></div>
        <button class="small" data-action="rim" ${dis}>Apply rim</button>
      </div>
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
      </dl>
    </details>
  </div>`;
}

function historyPanel() {
  const d = ui.state.draft;
  const items = d.edits.slice().reverse().map((e) => {
    const [cls, label] = CHANGE_KINDS[e.kind] || ["neutral", e.kind];
    return `<li data-testid="history-item"><span>#${e.index} <strong>${esc(e.op.op)}</strong> ${badge(cls, label)} ${e.validation_status !== "PASS" ? badge("bad", "invalid") : ""}</span>
      <button class="small ghost" data-action="revert" data-keep="${e.index}" ${busy() || e.index === d.edits.length ? "disabled" : ""} title="Keep edits 1..${e.index}">Revert here</button></li>`;
  }).join("");
  const dis = busy() ? "disabled" : "";
  return `<details class="panel tight section" data-section="history" data-testid="history-panel" ${openAttr("history")}>
    <summary><h3>Edit history</h3><span class="small muted">${d.edits.length ? `${d.edits.length} edit${d.edits.length === 1 ? "" : "s"}` : "none"}</span></summary>
    <div class="section-body">
      ${items ? `<ul class="history">${items}</ul>` : '<p class="small muted" style="margin:0">No layer edits.</p>'}
      <div class="row">
        <button class="small ghost" data-action="revert" data-keep="0" ${d.edits.length && !busy() ? "" : "disabled"}>Revert to base</button>
        <button class="small ghost tip" data-action="verify" ${dis} data-tip="Replay the saved edit log from the base and compare">Verify save/reload</button>
      </div>
    </div>
  </details>`;
}

// ------------------------------------------------------------------------------ canvas
// Inspection only: the wheel zooms, a drag (or Space+drag / middle drag) moves the zoomed view, a click
// marks a point (its mm coordinates are shown below the view).
function overlayGeometry() {
  const img = $("#edit-image");
  const canvas = $("#overlay");
  if (!img || !canvas) return null;
  const rect = img.getBoundingClientRect();
  const crect = canvas.getBoundingClientRect();
  const size = ui.state.draft.validation.outer_sheet_mm;
  return { img, canvas, rect, crect, size, dpr: window.devicePixelRatio || 1 };
}

function toMm(event) {
  const g = overlayGeometry();
  const x = ((event.clientX - g.rect.left) / g.rect.width) * g.size;
  const y = (1 - (event.clientY - g.rect.top) / g.rect.height) * g.size;
  return [r3(Math.min(Math.max(x, 0), g.size)), r3(Math.min(Math.max(y, 0), g.size))];
}

// mm on the sheet -> overlay canvas pixels, through the image's on-screen rectangle (any zoom/pan).
function toPx(point, g) {
  return [(g.rect.left - g.crect.left + (point[0] / g.size) * g.rect.width) * g.dpr,
          (g.rect.top - g.crect.top + (1 - point[1] / g.size) * g.rect.height) * g.dpr];
}

function drawOverlay() {
  const g = overlayGeometry();
  if (!g) return;
  const dpr = g.dpr;
  const w = Math.max(1, Math.round(g.crect.width * dpr));
  const h = Math.max(1, Math.round(g.crect.height * dpr));
  if (g.canvas.width !== w || g.canvas.height !== h) { g.canvas.width = w; g.canvas.height = h; }
  const ctx = g.canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.lineWidth = 2 * dpr;
  const point = ui.marker;
  if (point) {
    const [x, y] = toPx(point, g);
    ctx.strokeStyle = "#ff2d55";
    ctx.beginPath(); ctx.arc(x, y, 9 * dpr, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - 14 * dpr, y); ctx.lineTo(x + 14 * dpr, y); ctx.moveTo(x, y - 14 * dpr); ctx.lineTo(x, y + 14 * dpr); ctx.stroke();
  }
}

function bindCanvas() {
  const canvas = $("#overlay");
  if (!canvas) return;
  bindViewport("edit", canvas);
  $("#edit-image")?.addEventListener("load", () => applyView("edit"));
  applyView("edit");
  const state = ui.editView;
  let press = null;  // left button down: a click marks a point, a drag of the zoomed view moves it
  canvas.addEventListener("pointermove", (e) => {
    ui.hover = toMm(e);
    const c = $("#coords");
    if (c) c.textContent = `x ${ui.hover[0].toFixed(2)} mm · y ${ui.hover[1].toFixed(2)} mm`;
    if (!press) return;
    if (!press.moved && Math.hypot(e.clientX - press.x, e.clientY - press.y) < 4) return;
    press.moved = true;
    if (state.zoom !== null) {
      state.pan = [press.pan[0] + e.clientX - press.x, press.pan[1] + e.clientY - press.y];
      applyView("edit");
    }
  });
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    canvas.setPointerCapture(e.pointerId);
    press = { x: e.clientX, y: e.clientY, pan: [...state.pan], moved: false };
    if (state.zoom !== null) $("#edit-viewport")?.classList.add("panning");
  });
  canvas.addEventListener("pointerup", (e) => {
    if (!press) return;
    const moved = press.moved;
    press = null;
    $("#edit-viewport")?.classList.remove("panning");
    if (moved) return;
    ui.marker = toMm(e);
    $("#edit-viewport")?.setAttribute("data-marker", JSON.stringify(ui.marker));
    drawOverlay();
  });
  canvas.addEventListener("pointerleave", () => { ui.hover = null; });
}

// Arrow keys move through the layer list (front to back), like a list box.
function onLayerKey(event) {
  const row = event.target.closest?.('[data-testid="layer-row"]');
  if (!row) return;
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); row.click(); return; }
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (!step) return;
  event.preventDefault();
  const rows = [...document.querySelectorAll('[data-testid="layer-row"]')];
  const next = rows[rows.indexOf(row) + step];
  if (!next) return;
  next.click();
  $(`[data-testid="layer-row"][data-sheet="${CSS.escape(next.dataset.sheet)}"]`)?.focus();
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
    <div class="spread"><h2>Check printability</h2>${badge("outline", "estimate · not a slicer · not a print")}</div>
    <p class="muted" style="margin:0">Checks openings, slots and thin material against your printer. ${infoTip("Measures every opening, slot, gap and thin material of the current draft's exact export geometry against the printer parameters, plus build volume and layer quantization. Geometry is never changed: problems are reported for you to decide.")}</p>
    <details class="section subtle" data-section="est-process" ${openAttr("est-process")}>
      <summary><h4 style="margin:0">Printer &amp; process</h4><span class="small muted">${esc(ui.process.name)}${String(ui.process.source).startsWith("ASSUMPTION") ? " · generic defaults" : ""}</span></summary>
      <div class="section-body">${processForm("est")}</div></details>
    <div class="row"><button class="primary" data-action="run-printability" ${busy() ? "disabled" : ""}>Run estimate on the current draft</button>
      ${valid ? "" : '<span class="small muted">The draft is invalid; the estimate still runs, but the kit export will refuse it.</span>'}</div>
  </div>
  ${est ? `<div class="panel stack" data-testid="printability-result">
    <div class="spread"><h3 style="margin:0">Result ${badge(est.status === "NO_RISKS_FOUND" ? "ok" : est.status === "RISKS_REPORTED" ? "warn" : "bad", fdmLabel(est.status))} ${est.stale ? badge("warn", "out of date: the draft changed") : ""}</h3>
      <span class="small muted">${esc(est.process.name)} · ${esc(est.checked_utc)}</span></div>
    <p class="small">${est.counts.UNSUPPORTED} unsupported · ${est.counts.RISK} risk · ${est.counts.INFO} info. ${esc(est.basis)}.</p>
    ${findings.length ? `<p class="small"><a href="#" data-action="tab" data-tab="proposal" data-testid="to-resize">Check whether a larger sheet clears these (Physical size) →</a></p>` : ""}
    <p class="small" style="margin:0">Each finding is shown where it is in <strong>Issues</strong> below.</p>
    <details class="small" data-testid="findings-table"><summary>Findings table (${findings.length})</summary>${findingsTable(findings)}</details>
    ${info.length ? `<details><summary>${info.length} informational item(s)</summary>${infoList(info)}</details>` : ""}
    <details class="section subtle" data-section="est-maps" ${openAttr("est-maps", findings.length > 0)}>
      <summary><h4 style="margin:0">Layer maps</h4><span class="small muted">${est.map_urls.length} map${est.map_urls.length === 1 ? "" : "s"} · <span class="legend"><span><i style="background:#ff8c00"></i>risk</span><span><i style="background:#ff0000"></i>unsupported</span></span></span></summary>
      <div class="section-body"><div class="gallery compact">${est.map_urls.map((u) => `<figure><img class="img" src="${esc(u)}" alt="map" loading="lazy"><figcaption>${esc(u.split("/").pop().split("?")[0])}</figcaption></figure>`).join("")}</div></div></details>
    <p class="small muted">Physical result: ${badge("outline", "NOT VERIFIED")} — only your test print can confirm these thresholds.</p>
  </div>` : ""}
  ${est ? issuesPanel() : ""}`;
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
  const valid = ui.state.draft.status.printable && !ui.state.artwork?.differs_from_planned && !unsavedArt();
  // The build form is the main action until a kit matches the current draft; then it folds away.
  const needed = !latest || latest.stale || latest.status.cad !== "CAD_VALID";
  return `<section class="panel kit-build" data-testid="kit-build">
    <div class="spread"><h3 style="margin:0">${latest ? "Build a new print kit" : "Build the print kit"}</h3>
      <span class="small muted">${latest ? (latest.stale ? "the draft changed after the latest kit" : "printer/process settings, optional Bambu Studio project") : "export STEP/STL for every sheet"}</span></div>
    <div class="stack" style="margin-top:12px">
      <p class="muted small" style="margin:0">One STEP and one STL per sheet, checked by reopening every file. ${infoTip("Exports with the approved CAD pipeline, reopens every file, compares artwork, reviewed stack, exported geometry and reopened CAD, runs the FDM estimate and writes a guide and checksums. No frame, housing or glue is added; the rim stays optional. A new kit never replaces an earlier one.")}</p>
      <details class="section subtle" data-section="kit-process" ${openAttr("kit-process")}>
        <summary><h4 style="margin:0">Printer &amp; process</h4><span class="small muted">${esc(ui.process.name)}${String(ui.process.source).startsWith("ASSUMPTION") ? " · generic defaults" : ""}</span></summary>
        <div class="section-body">${processForm("kit")}</div></details>
      ${bambuOption()}
      <div class="row"><button class="${needed ? "primary" : ""}" data-action="build-kit" ${valid && !busy() ? "" : "disabled"}>${latest ? "Build new print kit" : "Build print kit"}</button>
        ${unsavedArt() ? '<span class="small no" data-testid="kit-unsaved-art">The artwork has unsaved changes; save or discard them first (Review layers → Artwork editor).</span>'
          : ui.state.artwork?.differs_from_planned ? '<span class="small no">The artwork was edited after these sheets were planned; plan from the edited artwork or undo the artwork edits (Review layers → Artwork editor).</span>'
          : valid ? "" : '<span class="small no">The draft is invalid (see Review layers); invalid drafts are never exported.</span>'}</div>
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
    case "tab":
      event.preventDefault();
      setTab(el.dataset.tab);
      if (el.dataset.focus === "artwork") ui.sections.artwork = true;
      render();
      if (el.dataset.focus === "artwork") $('[data-testid="artwork-panel"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
      break;
    case "dismiss-error": ui.error = null; render(); break;
    case "create": createProject(); break;
    case "propose": submit(`${project}/propose`, {}); break;
    case "apply-details": {
      const p = ui.state.proposal;
      const decisions = {};
      for (const d of p.details || []) decisions[d.id] = detailChoice(p, d);
      submit(`${project}/propose`, { detail_decisions: decisions, plan: p.status !== "ARTWORK_READY_NOT_PLANNED" });
      ui.detailChoices = {};
      break;
    }
    case "cleanup-view": ui.cleanupView = el.dataset.view; render(); break;
    case "apply-cleanup": {
      const p = ui.state.proposal;
      submit(`${project}/propose`, { clean_speckles: ui.cleanupChoice ?? p.cleanup.enabled, plan: p.status !== "ARTWORK_READY_NOT_PLANNED" });
      ui.cleanupChoice = null;
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
    case "select-sheet": ui.sheet = el.dataset.sheet; ui.marker = null; if (!ui.view.startsWith("changes")) ui.view = "sheet"; render(); break;
    case "view": ui.view = el.dataset.view; render(); break;
    case "cancel-pending": ui.pending = null; render(); break;
    case "apply-pending": if (ui.pending) editOp(ui.pending); break;
    case "propose-op": ui.pending = JSON.parse(el.dataset.op); if (ui.pending.sheet) ui.sheet = ui.pending.sheet; ui.view = "sheet"; setTab("review"); render(); break;
    case "locate":
      ui.sheet = el.dataset.sheet; ui.marker = [Number(el.dataset.x), Number(el.dataset.y)]; ui.pending = null; ui.view = "sheet";
      setTab("review"); render(); $("#edit-viewport")?.scrollIntoView({ behavior: "smooth", block: "center" }); break;
    case "move": editOp({ op: "move", sheet, to: Number(el.dataset.to) }); break;
    case "recolor": editOp({ op: "recolor", sheet, color: $("#p-color").value.toUpperCase() }); break;
    case "thickness": editOp({ op: "thickness", sheet, mm: Number($("#p-thick").value) }); break;
    case "rim": {
      const on = $("#p-rim").checked;
      const target = $("#p-rim-all").checked ? "*" : sheet;  // "*" = every layer, in one edit
      editOp(on ? { op: "rim", sheet: target, enabled: true, width_mm: Number($("#p-rim-w").value), total_height_mm: Number($("#p-rim-h").value) } : { op: "rim", sheet: target, enabled: false });
      break;
    }
    case "undo": submit(`${project}/undo`, {}); break;
    case "revert": submit(`${project}/undo`, { keep: Number(el.dataset.keep) }); break;
    case "verify": submit(`${project}/verify`, {}); break;
    case "reload": await loadState(); render(); break;
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
    case "art-mode": {
      if (el.dataset.mode !== "eyedropper" && el.dataset.mode !== ui.art.mode && !(await finalizeShape())) break;
      const s = ui.art;  // a pending shape became an edit: a new tool starts clean
      if (el.dataset.mode === "eyedropper") s.prevMode = s.mode === "eyedropper" ? s.prevMode : s.mode;
      s.mode = el.dataset.mode;
      s.gap = null;
      s.stroke = null;
      render();
      break;
    }
    case "art-clear-box": ui.art.box = null; render(); break;
    case "art-gap-path": {  // Close gap: straight or curved connection between the snapped ends
      const d = ui.art.draw;
      if (!d || d.kind !== "gap" || d.path === el.dataset.path) break;
      const [a, b] = [d.points[0], d.points[d.points.length - 1]];
      Object.assign(d, el.dataset.path === "cubic" ? { path: "cubic", points: [a, ...bentControls(a, b), b] } : { path: "line", points: [a, b] });
      await artworkDraw();
      break;
    }
    case "art-straighten": {
      const d = ui.art.draw;
      if (!d || d.path !== "cubic") break;
      d.points.splice(1, 2, ...bentControls(d.points[0], d.points[3], 0));
      await artworkDraw();
      break;
    }
    case "zoom-fit": zoomView(el.dataset.viewKey, "fit"); break;
    case "art-color": await artworkChoose(el.dataset.color, "palette"); break;
    case "art-cancel": ui.art = keepArtView(); render(); break;
    case "insp-select": selectIssue(el.dataset.issue); break;
    case "insp-goto": selectIssue(el.dataset.issue); break;  // the same place on another layer
    case "insp-review": {
      const sel = ui.issues?.issues?.find((i) => i.id === ui.insp.selected);
      if (!sel?.key) break;
      const keys = el.dataset.scope === "group" ? [sel, ...inspGroupMates(ui.issues, sel)].map((i) => i.key) : [sel.key];
      await inspReview(keys, el.dataset.state);
      break;
    }
    case "insp-layer": inspShowLayer(el.dataset.key); break;
    case "insp-step": {
      const list = ui.issues && !ui.issues.error ? filteredIssues(ui.issues) : [];
      if (!list.length) break;
      const at = list.findIndex((i) => i.id === ui.insp.selected);
      const step = Number(el.dataset.step);
      selectIssue(list[at < 0 ? (step > 0 ? 0 : list.length - 1) : (at + step + list.length) % list.length].id);
      break;
    }
    case "insp-edit-layer": inspEditLayer(ui.issues?.issues?.find((i) => i.id === ui.insp.selected)); break;
    case "insp-edit-artwork": inspEditArtwork(ui.issues?.issues?.find((i) => i.id === ui.insp.selected)); break;
    case "art-done": await finalizeShape(); break;
    case "art-remove": discardShape(true); break;
    case "art-session": await artworkSession(el.dataset.session); break;
    case "art-discard-ask": ui.art.confirmDiscard = true; render(); $('[data-testid="art-discard-cancel"]')?.focus(); break;
    case "art-discard-cancel": ui.art.confirmDiscard = false; render(); break;
    case "art-discard-confirmed": await artworkSession("discard"); break;
    case "art-undo": await artworkPost("undo", el.dataset.keep === undefined ? {} : { keep: Number(el.dataset.keep) }); break;
    case "art-verify": await artworkPost("verify", {}); break;
    case "art-replan": submit(`${project}/artwork/replan`, {}); break;
    case "art-fit-open": ui.fit = { ...freshFit(), open: true }; await fitPreview(); break;
    case "art-fit-close": ui.fit = freshFit(); render(); break;
    case "art-fit-auto": ui.fit.acceptLoss = false; await fitPreview(); break;
    case "art-fit-accept": ui.fit.acceptLoss = !ui.fit.acceptLoss; await fitCommit(); break;
    case "art-fit-bg": if (ui.fit.manual) { ui.fit.manual.background = el.dataset.color; await fitCommit(); } break;
    case "art-fit-apply": await fitApply(); break;
    case "toggle-kit-build": ui.kitBuildOpen = !ui.kitBuildOpen; render(); break;
    default: break;
  }
}

function onChange(event) {
  const el = event.target;
  if (el.id === "project-select") {
    location.hash = el.value ? `#/${encodeURIComponent(el.value)}` : "#/";
  } else if (["insp-kind", "insp-severity", "insp-status"].includes(el.id)) {
    ui.insp[el.id.slice(5)] = el.value;
    render();
  } else if (el.id === "art-picker") {
    artworkChoose(el.value.toUpperCase(), "picker");
  } else if (el.id === "art-width") {
    const width = Number(el.value);
    if (!(width > 0 && width <= 50)) { el.value = String(ui.art.width); return; }
    ui.art.width = Math.round(width * 1000) / 1000;
    if (ui.art.draw) artworkDraw();  // same stroke, new thickness
    else render();
  } else if (el.id === "art-angle" || el.id === "art-length") {
    const angle = Number($("#art-angle")?.value);
    const length = Number($("#art-length")?.value);
    if (Number.isFinite(angle) && length > 0) setShapeGeometry(angle, length);
  } else if (el.id === "f-plan-later") {
    ui.planLater = el.checked;  // no re-render: that would reset the other import fields
    const button = $('[data-action="create"]');
    if (button) button.textContent = ui.planLater ? "Import and prepare artwork" : "Import and propose layers";
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
  } else if (el.id === "cleanup-on") {
    ui.cleanupChoice = el.checked;
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
  }
}

// ------------------------------------------------------------------------------- start
async function main() {
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  document.addEventListener("keydown", onLayerKey);
  // Remember sections the user opens or closes (only clicks: renders also toggle <details>).
  document.addEventListener("click", (e) => {
    const details = e.target.closest("summary")?.parentElement;
    if (details?.dataset.section) ui.sections[details.dataset.section] = !details.open;
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => { applyView("edit"); applyArtView(); applyView("insp"); });
  document.addEventListener("keydown", onArtKey);
  document.addEventListener("keyup", onArtKey);
  document.addEventListener("keydown", onArtShortcut);
  window.addEventListener("beforeunload", (e) => {  // closing or reloading with unsaved artwork changes
    if (!unsavedArt()) return;
    e.preventDefault();
    e.returnValue = "";
  });
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
