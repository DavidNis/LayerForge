// Drives the Issue inspector (processed image | layer, cause, same place, review) of the real LayerForge app in a headless Chromium browser (Edge or
// Chrome) through the DevTools protocol: real clicks, wheel zoom and drags.  No npm packages:
// Node >= 22 (built-in WebSocket).  Same bootstrap as drive_artwork.mjs.
//
//   node tests/e2e/drive_issue_review.mjs <app url> <browser exe> <unused> <screenshot dir> <reviewed project>
//
// The project is prepared through the API by tests/test_issue_review.py.  Prints one JSON line.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const [url, browserExe, imagePath, shotDir] = process.argv.slice(2);
const results = { steps: [] };
mkdirSync(shotDir, { recursive: true });

const profile = mkdtempSync(join(tmpdir(), "lf-e2e-"));
const browser = spawn(browserExe, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--window-size=1440,1000", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
const wsUrl = await new Promise((ok, fail) => {
  let buf = "";
  const timer = setTimeout(() => fail(new Error("browser did not start: " + buf)), 30000);
  browser.stderr.on("data", (d) => {
    buf += d;
    const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
    if (m) { clearTimeout(timer); ok(m[1]); }
  });
});
const ws = new WebSocket(wsUrl);
await new Promise((ok) => ws.addEventListener("open", ok, { once: true }));
let nextId = 1;
const waiting = new Map();
const consoleErrors = [];
const dialogs = [];
let dialogAccept = true;
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, fail } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result);
  } else if (msg.method === "Page.javascriptDialogOpening") {  // confirm() / beforeunload: recorded, then answered
    dialogs.push({ type: msg.params.type, message: msg.params.message });
    send("Page.handleJavaScriptDialog", { accept: dialogAccept });
  } else if (msg.method === "Runtime.exceptionThrown") {
    consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  } else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
    consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));
  }
});
let session;
const send = (method, params = {}, sessionId = session) => new Promise((ok, fail) => {
  const id = nextId++;
  waiting.set(id, { ok, fail });
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const { targetId } = await send("Target.createTarget", { url: "about:blank" }, null);
({ sessionId: session } = await send("Target.attachToTarget", { targetId, flatten: true }, null));
await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`evaluate failed: ${expression}: ${r.exceptionDetails.exception?.description}`);
  return r.result.value;
}
async function shot(name) {
  await evaluate("window.scrollTo(0, 0)");
  await sleep(100);
  const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, "base64"));
}
// One element only (for the user guide), from the page laid out at full height.
async function shotEl(name, selector) {
  const r = await evaluate(`(() => { const b = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x: b.left + scrollX, y: b.top + scrollY, w: b.width, h: b.height}; })()`);
  const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: Math.max(0, r.x - 4), y: Math.max(0, r.y - 4), width: r.w + 8, height: r.h + 8, scale: 1 } });
  writeFileSync(join(shotDir, `guide_${name}.png`), Buffer.from(data, "base64"));
}
async function waitFor(expression, what, timeoutMs = 60000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(200);
  }
  await shot(`timeout_${what.replace(/\W+/g, "_")}`);
  throw new Error(`timed out waiting for: ${what}`);
}
const text = (sel) => evaluate(`document.querySelector(${JSON.stringify(sel)})?.innerText ?? ""`);
const count = (sel) => evaluate(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
const bodyHas = (s) => `document.body.innerText.includes(${JSON.stringify(s)})`;
const idle = `!document.querySelector('[data-testid="job-banner"]')`;
async function clickSel(selector) {
  const ok = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el || el.disabled) return false; el.scrollIntoView({block: "center"}); el.click(); return true; })()`);
  if (!ok) throw new Error(`cannot click ${selector}: ${await evaluate(`document.querySelector(${JSON.stringify(selector)})?.outerHTML ?? "not found"`)}`);
}
// Screen point of an artwork pixel centre, through the image's on-screen rectangle (any zoom/pan).
async function artPoint([row, col], scroll = true) {
  if (scroll) {
    await evaluate(`document.querySelector("#art-viewport").scrollIntoView({block: "center"})`);
    await sleep(150);
  }
  const r = await evaluate(`(() => { const i = document.querySelector("#art-image"); const b = i.getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height, nw: i.naturalWidth, nh: i.naturalHeight}; })()`);
  return { x: r.x + ((col + 0.5) / r.nw) * r.w, y: r.y + ((row + 0.5) / r.nh) * r.h };
}
async function viewportRect() {
  return evaluate(`(() => { const b = (document.querySelector("#edit-viewport") || document.querySelector("#art-viewport")).getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height}; })()`);
}
const MODIFIERS = { shift: 8, ctrl: 2 };
async function mouse(type, p, buttons = 1, button = "left", modifiers = 0) {
  await send("Input.dispatchMouseEvent", { type, x: p.x, y: p.y, button: type === "mouseMoved" && !buttons ? "none" : button, buttons, modifiers, clickCount: type === "mouseMoved" ? 0 : 1 });
}
async function artClick(px, modifiers = 0) {
  const p = await artPoint(px);
  await mouse("mouseMoved", p, 0, "left", modifiers);
  await mouse("mousePressed", p, 1, "left", modifiers);
  await mouse("mouseReleased", p, 0, "left", modifiers);
}
// Drag between two screen points with a button (left with Space held, or middle).
async function screenDrag(a, b, button = "left") {
  const mask = button === "middle" ? 4 : 1;
  await mouse("mouseMoved", a, 0);
  await mouse("mousePressed", a, mask, button);
  for (let i = 1; i <= 5; i++) await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / 5, y: a.y + ((b.y - a.y) * i) / 5 }, mask, button);
  await mouse("mouseReleased", b, 0, button);
}
async function key(type, code, keyName, vk) {
  await send("Input.dispatchKeyEvent", { type, code, key: keyName, windowsVirtualKeyCode: vk });
}
const stageTransform = () => evaluate(`document.querySelector("#art-stage").style.transform`);
const zoomLabel = () => text('[data-testid="art-zoom-label"]');
const selectionCount = () => evaluate(`document.querySelector('[data-testid="art-selection-count"]')?.innerText ?? ""`);
async function artDrag(from, to) {
  const a = await artPoint(from);
  const b = await artPoint(to);
  await mouse("mouseMoved", a, 0);
  await mouse("mousePressed", a);
  for (let i = 1; i <= 5; i++) await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / 5, y: a.y + ((b.y - a.y) * i) / 5 });
  await mouse("mouseReleased", b, 0);
}
function step(name, detail = {}) {
  results.steps.push({ name, ...detail });
  console.error(`[e2e] ${name} ${JSON.stringify(detail)}`);
}
const [, , , , projectSheets, projectCandidate, projectFailed] = process.argv.slice(2);
const issuesApi = (name) => evaluate(`fetch("/api/projects/${name}/issues").then((r) => r.json())`);
const setSelect = (id, value) => evaluate(`(() => { const s = document.querySelector("#${id}"); s.value = ${JSON.stringify(value)}; s.dispatchEvent(new Event("change", {bubbles: true})); })()`);
const rows = () => count('[data-testid="insp-row"]');
const inspZoom = () => text('[data-testid="insp-zoom-label"]');
// Screen point of a sheet-mm point in a view (image extent in mm), and the viewport centre.
async function screenOf(imageSel, extent, [x, y]) {
  const r = await evaluate(`(() => { const i = document.querySelector(${JSON.stringify(imageSel)}); const b = i.getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height}; })()`);
  return { x: r.x + ((x - extent[0]) / (extent[2] - extent[0])) * r.w, y: r.y + ((extent[3] - y) / (extent[3] - extent[1])) * r.h };
}
async function centreOf(sel) {
  return evaluate(`(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2}; })()`);
}
const centreBox = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
// How much of the viewport the box fills along its limiting side (the zoom target is 0.45).
async function fillRatio(imageSel, extent, b, viewportSel) {
  const a = await screenOf(imageSel, extent, [b[0], b[3]]);
  const c = await screenOf(imageSel, extent, [b[2], b[1]]);
  const v = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(viewportSel)}).getBoundingClientRect(); return {w: r.width, h: r.height}; })()`);
  return Math.max((c.x - a.x) / v.w, (c.y - a.y) / v.h);
}
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
// The overlay canvas pixel [r, g, b, a] under a sheet-mm point (checks that the drawing is where the issue is).
async function overlayAt(extent, point) {
  const p = await screenOf("#insp-image", extent, point);
  return evaluate(`(() => { const c = document.querySelector("#insp-overlay"); const b = c.getBoundingClientRect();
    const k = c.width / b.width; const x = Math.round((${p.x} - b.left) * k), y = Math.round((${p.y} - b.top) * k);
    if (x < 0 || y < 0 || x >= c.width || y >= c.height) return null;
    return [...c.getContext("2d").getImageData(x, y, 1, 1).data]; })()`);
}
const overlayEmpty = () => evaluate(`(() => { const c = document.querySelector("#insp-overlay"); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) if (d[i]) return false; return true; })()`);
async function selectRow(title) {
  await evaluate(`(() => { const b = [...document.querySelectorAll('[data-testid="insp-row"]')].find((x) => x.innerText.includes(${JSON.stringify(title)})); b.scrollIntoView({block: "center"}); b.click(); })()`);
  await waitFor(`document.querySelector('[data-testid="insp-title"]')?.innerText === ${JSON.stringify(title)}`, `select ${title}`);
  await sleep(500);  // the view zooms once the image is loaded
}
async function waitImage(sel) {
  await waitFor(`document.querySelector(${JSON.stringify(sel)})?.complete && document.querySelector(${JSON.stringify(sel)}).naturalWidth > 0`, `image ${sel}`);
}
async function chip(key) {
  await clickSel(`[data-testid="insp-layer-chip"][data-key="${key}"]`);
  await waitFor(`document.querySelector('[data-testid="insp-layer-chip"].active')?.dataset.key === ${JSON.stringify(key)}`, `layer ${key}`);
  await waitImage("#insp-image");
  await sleep(300);
}
const chips = () => evaluate(`[...document.querySelectorAll('[data-testid="insp-layer-chip"]')].map((c) => ({key: c.dataset.key, text: c.innerText.replace(/\\s+/g, " ").trim(), ok: Boolean(c.querySelector(".insp-ok")), active: c.classList.contains("active")}))`);
async function showInspector() {
  await evaluate(`document.querySelector('[data-testid="issue-inspector"]').scrollIntoView({block: "start"})`);
  await sleep(150);
}
async function alignment(extent, inside, outside) {
  return { zoom: await inspZoom(), inside: await overlayAt(extent, inside), outside: await overlayAt(extent, outside) };
}

// Where a sheet-mm point is on screen relative to its own viewport, in each view (synchronized views
// give the same offsets), and the size the image is drawn at.
async function viewOffsets(point, extent) {
  const out = {};
  for (const [key, img, vp] of [["layer", "#insp-image", "#insp-viewport"], ["art", "#insp-art-image", "#insp-art-viewport"]]) {
    const p = await screenOf(img, extent, point);
    const v = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(vp)}).getBoundingClientRect(); const i = document.querySelector(${JSON.stringify(img)}).getBoundingClientRect(); return {x: r.left, y: r.top, w: i.width}; })()`);
    out[key] = { dx: p.x - v.x, dy: p.y - v.y, w: v.w };
  }
  out.diff = Math.hypot(out.layer.dx - out.art.dx, out.layer.dy - out.art.dy);
  out.scale_ratio = out.layer.w / out.art.w;
  return out;
}
// Overlay pixel of the processed-image canvas under a sheet-mm point.
async function artOverlayAt(extent, point) {
  const p = await screenOf("#insp-art-image", extent, point);
  return evaluate(`(() => { const c = document.querySelector("#insp-art-overlay"); const b = c.getBoundingClientRect();
    const k = c.width / b.width; const x = Math.round((${p.x} - b.left) * k), y = Math.round((${p.y} - b.top) * k);
    if (x < 0 || y < 0 || x >= c.width || y >= c.height) return null;
    return [...c.getContext("2d").getImageData(x, y, 1, 1).data]; })()`);
}
// Color of the shown processed image (padded to the sheet extent) at a sheet-mm point.
async function artPixel(extent, [x, y]) {
  return evaluate(`(() => { const i = document.querySelector("#insp-art-image"); const c = document.createElement("canvas");
    c.width = i.naturalWidth; c.height = i.naturalHeight; const g = c.getContext("2d"); g.drawImage(i, 0, 0);
    const px = Math.floor(((${x} - ${extent[0]}) / ${extent[2] - extent[0]}) * i.naturalWidth), py = Math.floor(((${extent[3]} - ${y}) / ${extent[3] - extent[1]}) * i.naturalHeight);
    const d = g.getImageData(px, py, 1, 1).data; return "#" + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, "0").toUpperCase()).join(""); })()`);
}
const viewStates = () => evaluate(`["#insp-stage", "#insp-art-stage"].map((s) => { const e = document.querySelector(s); return [e.style.transform, e.style.width]; })`);
const cardState = () => evaluate(`document.querySelector('[data-testid="insp-review"]')?.dataset.state ?? null`);
async function waitArt() {
  await waitFor(`document.querySelector("#insp-art-image")?.naturalWidth > 0 && document.querySelector("#insp-art-image").complete`, "processed image");
  await sleep(300);
}

try {
  // 1. The reviewed project: the inspector shows the processed image beside layer 1, same extent and size.
  await send("Page.navigate", { url: `${url}#/${projectSheets}/review` });
  await waitFor(`document.querySelectorAll('[data-testid="insp-row"]').length > 0 && ${idle}`, "issues listed", 180000);
  await waitImage("#insp-image");
  await waitArt();
  let data = await issuesApi(projectSheets);
  const outer = data.images.find((i) => i.key === "front").extent_mm;
  const layer1 = data.sheets[data.sheets.length - 1].id, layer2 = data.sheets[data.sheets.length - 2].id;
  const slot = data.issues.find((i) => i.sheet === layer1 && i.kind === "opening_may_close");
  const mate = data.issues.find((i) => i.sheet === layer2 && i.group === slot.group);
  results.start = {
    art_views: await count('[data-testid="insp-art-view"]'), art_caption: await text('[data-testid="insp-art-view"] figcaption'),
    sizes: await evaluate(`[document.querySelector("#insp-art-image").naturalWidth, document.querySelector("#insp-image").naturalWidth]`),
    shown: await text('[data-testid="insp-shown"]'), group_sheets: data.groups.find((g) => g.id === slot.group).sheets.length,
    mate: Boolean(mate),
  };
  // The processed image (not the uploaded original): its pixels inside the aperture equal the planned target.
  const target = data.images.find((i) => i.key === "artwork");
  results.start.target_url = target.url;
  results.start.same_as_target = await evaluate(`new Promise((ok) => { const t = new Image(); t.onload = () => {
      const a = document.querySelector("#insp-art-image"); const band = (a.naturalWidth - t.naturalWidth) / 2;
      const c1 = document.createElement("canvas"); c1.width = a.naturalWidth; c1.height = a.naturalHeight; const g1 = c1.getContext("2d"); g1.drawImage(a, 0, 0);
      const c2 = document.createElement("canvas"); c2.width = t.naturalWidth; c2.height = t.naturalHeight; const g2 = c2.getContext("2d"); g2.drawImage(t, 0, 0);
      const d1 = g1.getImageData(band, band, t.naturalWidth, t.naturalHeight).data, d2 = g2.getImageData(0, 0, t.naturalWidth, t.naturalHeight).data;
      let diff = 0; for (let i = 0; i < d2.length; i++) if (d1[i] !== d2[i]) diff++; ok({band, diff}); }; t.src = ${JSON.stringify(target.url)}; })`);
  await showInspector();
  await shot("r0_dual_view");

  // 2. Select the narrow opening on layer 1: both views focus the same place; the processed image shows
  //    what it reveals (hatched) and the image region (outlined); the card names the cause and the colors.
  await selectRow("Opening may close");
  await waitArt();
  const centre = centreBox(slot.bbox_mm);
  const inSlot = [70, 40];
  results.select = {
    offsets: await viewOffsets(centre, outer),
    cause: await evaluate(`document.querySelector('[data-testid="insp-cause"]')?.dataset.cause ?? null`), api_cause: slot.feature?.cause,
    cause_text: await text('[data-testid="insp-cause"]'), reveals: await text('[data-testid="insp-reveals"]'),
    api_colors: (slot.feature?.regions || []).map((r) => r.color),
    pixel_under: await artPixel(outer, inSlot),
    overlay_in: await artOverlayAt(outer, inSlot), overlay_far: await artOverlayAt(outer, [70, 52]),
    layer_overlay_in: await overlayAt(outer, inSlot),
    group: await text('[data-testid="insp-group"]'), mates: await count('[data-testid="insp-mate"]'),
    measured: await text('[data-testid="insp-measured"]'), threshold: await text('[data-testid="insp-threshold"]'),
    where: await text('[data-testid="insp-sheet"]'), state: await cardState(),
  };
  results.select.states_before = await viewStates();
  await showInspector();
  await shot("r1_selected_opening");
  results.select.states_after_shot = await viewStates();
  await shotEl("insp_dual_selected", '[data-testid="issue-inspector"]');
  results.select.states_after_el = await viewStates();
  results.select.offsets_after_shots = await viewOffsets(centre, outer);

  // 3. Wheel zoom on the processed image and a middle-button pan on the layer: the other view follows.
  await showInspector();
  const ac = await centreOf("#insp-art-viewport");
  for (let i = 0; i < 3; i++) { await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: ac.x + 30, y: ac.y - 20, deltaX: 0, deltaY: -120 }); await sleep(80); }
  await sleep(250);
  results.wheel = { offsets: await viewOffsets(centre, outer), zoom: await inspZoom(), states: await viewStates() };
  await showInspector();
  const lc = await centreOf("#insp-viewport");
  await screenDrag(lc, { x: lc.x + 60, y: lc.y + 35 }, "middle");
  await sleep(250);
  results.pan = { offsets: await viewOffsets(centre, outer), states: await viewStates() };
  // A full-page screenshot resizes the page for a moment; a wheel zoom on the layer afterwards keeps both views in step.
  await shot("r1b_zoomed");
  await showInspector();
  const lc2 = await centreOf("#insp-viewport");
  for (let i = 0; i < 2; i++) { await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: lc2.x - 40, y: lc2.y + 25, deltaX: 0, deltaY: 120 }); await sleep(80); }
  await sleep(250);
  results.after_resize = { offsets: await viewOffsets(centre, outer), states: await viewStates() };

  // 4. The same place on layer 2: one click, the layer changes, the place stays in view.
  await clickSel('[data-testid="insp-mate"]');
  await waitFor(`document.querySelector('[data-testid="insp-shown"]')?.innerText.startsWith("Layer 2")`, "mate layer");
  await waitImage("#insp-image");
  await sleep(500);
  results.mate = { shown: await text('[data-testid="insp-shown"]'), title: await text('[data-testid="insp-title"]'),
                   card_key: await evaluate(`document.querySelector('[data-testid="insp-card"]')?.dataset.key`), api_key: mate?.key,
                   offsets: await viewOffsets(centre, outer), back_mates: await text('[data-testid="insp-group"]') };
  await shot("r2_same_place_layer2");

  // 5. Review: Accept risk on this one, then "Same for all layers here"; the warning stays; reload keeps it.
  await clickSel('[data-testid="insp-review-accept_risk"]');
  await waitFor(`document.querySelector('[data-testid="insp-review"]')?.dataset.state === "accept_risk"`, "accepted");
  results.review = { accepted_note: await text('[data-testid="insp-accepted"]'), group_button: await count('[data-testid="insp-review-group"]'),
                     badge: await text('[data-testid="insp-card"] .badge'), rows_before: await rows() };
  await clickSel('[data-testid="insp-review-group"]');
  await waitFor(`!document.querySelector('[data-testid="insp-review-group"]')`, "group decided");
  data = await issuesApi(projectSheets);
  results.review.api_states = data.issues.filter((i) => i.group === slot.group).map((i) => i.review.state);
  results.review.api_severities = data.issues.filter((i) => i.group === slot.group).map((i) => i.severity);
  results.review.tags = await count('[data-testid="insp-row"] .insp-tag.accept');
  results.review.summary = await text('[data-testid="insp-decided"]');
  await setSelect("insp-status", "accept_risk");
  await sleep(200);
  results.review.filtered_rows = await rows();
  await setSelect("insp-status", "");
  await sleep(200);
  await showInspector();
  await shot("r3_accepted");
  await send("Page.reload");
  await waitFor(`document.querySelectorAll('[data-testid="insp-row"]').length > 0 && ${idle}`, "issues after reload", 180000);
  await waitImage("#insp-image");
  await selectRow("Opening may close");
  results.reload = { state: await cardState(), title: await text('[data-testid="insp-title"]'),
                     badge: await text('[data-testid="insp-card"] .badge'), rows: await rows(),
                     api_rows: (await issuesApi(projectSheets)).issues.filter((i) => i.sheet === layer1).length };
  await clickSel('[data-testid="insp-review-fix_later"]');
  await waitFor(`document.querySelector('[data-testid="insp-review"]')?.dataset.state === "fix_later"`, "fix later");
  await clickSel('[data-testid="insp-review-needs_review"]');
  await waitFor(`document.querySelector('[data-testid="insp-review"]')?.dataset.state === "needs_review"`, "needs review");
  results.reload.after_reset = (await issuesApi(projectSheets)).issues.find((i) => i.key === slot.key).review.state;

  // 6. Previous / next keep both views focused on the selected issue; Fit resets both.
  await clickSel('[data-testid="insp-next"]');
  await sleep(600);
  results.next_id = await evaluate(`document.querySelector('[data-testid="insp-list"] li.active button')?.dataset.issue`);
  const next = (await issuesApi(projectSheets)).issues.find((i) => i.id === results.next_id);
  results.next = { title: await text('[data-testid="insp-title"]'), offsets: next?.bbox_mm ? await viewOffsets(centreBox(next.bbox_mm), outer) : null };
  await clickSel('[data-testid="insp-zoom-fit"]');
  await sleep(300);
  results.fit = await evaluate(`["#insp-stage", "#insp-art-stage"].map((s) => document.querySelector(s).style.transform)`);
  results.fit_offsets = await viewOffsets([outer[2] / 2, outer[3] / 2], outer);

  // 7. Narrow window: the views and the card stack instead of squeezing.
  await send("Emulation.setDeviceMetricsOverride", { width: 600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  results.narrow = await evaluate(`(() => { const a = document.querySelector('[data-testid="insp-art-view"]').getBoundingClientRect(),
      l = document.querySelector("#insp-viewport").getBoundingClientRect(), s = document.querySelector(".insp-side").getBoundingClientRect();
      return {art_above_layer: a.bottom <= l.top + 30, side_below: s.top >= l.bottom - 1, art_w: a.width, page_w: document.documentElement.scrollWidth}; })()`);
  await shot("r4_narrow");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(300);

  results.console_errors = consoleErrors;
  results.ok = true;
} catch (error) {
  results.ok = false;
  results.error = String(error.stack || error);
  results.console_errors = consoleErrors;
}

console.log(JSON.stringify(results));
try { ws.close(); } catch {}
browser.kill();
await sleep(500);
try { rmSync(profile, { recursive: true, force: true }); } catch {}
process.exit(results.ok ? 0 : 1);
