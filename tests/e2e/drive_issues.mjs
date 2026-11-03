// Drives the Issue inspector of the real LayerForge app in a headless Chromium browser (Edge or
// Chrome) through the DevTools protocol: real clicks, wheel zoom and drags.  No npm packages:
// Node >= 22 (built-in WebSocket).  Same bootstrap as drive_artwork.mjs.
//
//   node tests/e2e/drive_issues.mjs <app url> <browser exe> <unused> <screenshot dir> <reviewed project> <candidate project> <failed project>
//
// The projects are prepared through the API by tests/test_issue_inspector.py.  Prints one JSON line.

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

try {
  // 1. A reviewed project: a 0.6 mm slot, a floating disc, a disc on a 1.0 mm bar and a 0.5 mm spike on layer 1,
  //    a 0.5 mm slot on layer 2. The inspector opens on layer 1 with its problems outlined and numbered.
  await send("Page.navigate", { url: `${url}#/${projectSheets}/review` });
  await waitFor(`document.querySelectorAll('[data-testid="insp-row"]').length > 0 && ${idle}`, "issues listed", 180000);
  await waitImage("#insp-image");
  const data = await issuesApi(projectSheets);
  const front = data.images.find((i) => i.key === "front").extent_mm;
  const layer1 = data.sheets[data.sheets.length - 1].id, layer2 = data.sheets[data.sheets.length - 2].id;
  const on = (sheet) => data.issues.filter((i) => i.sheet === sheet);
  const find = (sheet, kind) => data.issues.find((i) => i.sheet === sheet && i.kind === kind);
  results.start = { shown: await text('[data-testid="insp-shown"]'), rows: await rows(), api_layer1: on(layer1).length,
                    chips: await chips(), summary: await text('[data-testid="insp-summary"]') };
  await showInspector();
  await shot("i0_layer1_overview");
  await shotEl("insp_layer1", '[data-testid="issue-inspector"]');

  // 2. The narrow opening: zoomed and centred; the overlay is drawn exactly on it (and not beside it).
  const slot = find(layer1, "opening_may_close");
  await selectRow("Opening may close");
  results.slot = {
    title: await text('[data-testid="insp-title"]'), measured: await text('[data-testid="insp-measured"]'),
    threshold: await text('[data-testid="insp-threshold"]'), where: await text('[data-testid="insp-sheet"]'),
    shown: await text('[data-testid="insp-shown"]'), fill: await fillRatio("#insp-image", front, slot.bbox_mm, "#insp-viewport"),
    layer_button: await evaluate(`document.querySelector('[data-testid="insp-edit-layer"]')?.className ?? null`),
    centre_offset_px: dist(await screenOf("#insp-image", front, centreBox(slot.bbox_mm)), await centreOf("#insp-viewport")),
  };
  const slotIn = [70, 40], slotOut = [70, 42];  // inside the 0.6 mm slot; 1.7 mm above its edge
  results.slot.align = [await alignment(front, slotIn, slotOut)];
  await showInspector();
  await shot("i1_narrow_opening");
  await shotEl("insp_narrow_opening", '[data-testid="issue-inspector"]');
  // Other zoom levels: wheel out, then Fit; the drawing follows the image.
  const vc = await centreOf("#insp-viewport");
  for (let i = 0; i < 3; i++) { await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: vc.x, y: vc.y, deltaX: 0, deltaY: 120 }); await sleep(80); }
  await sleep(200);
  results.slot.align.push(await alignment(front, slotIn, [70, 46]));
  await clickSel('[data-testid="insp-zoom-fit"]');
  await sleep(300);
  results.slot.align.push(await alignment(front, slotIn, [70, 60]));
  await showInspector();
  await shot("i1b_narrow_opening_fit");

  // 3. The narrow retained connection: the 1.0 mm bar is highlighted (the disc it holds only dashed).
  const neck = find(layer1, "narrow_connection");
  await selectRow("Material held by a too-narrow connection");
  results.neck = { measured: await text('[data-testid="insp-measured"]'), threshold: await text('[data-testid="insp-threshold"]'),
                   fill: await fillRatio("#insp-image", front, neck.bbox_mm, "#insp-viewport"),
                   bar: await overlayAt(front, [33, 175]), disc_centre: await overlayAt(front, [60, 175]) };
  await showInspector();
  await shot("i2_narrow_connection");
  await shotEl("insp_narrow_connection", '[data-testid="issue-inspector"]');

  // 4. Zoom in on the disc with the wheel, pan with the middle button, click exactly on it.
  const disc = find(layer1, "floating_material");
  await clickSel('[data-testid="insp-zoom-fit"]');
  await sleep(200);
  let p = await screenOf("#insp-image", front, centreBox(disc.bbox_mm));
  for (let i = 0; i < 4; i++) { await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: p.x, y: p.y, deltaX: 0, deltaY: -120 }); await sleep(60); }
  const vc2 = await centreOf("#insp-viewport");
  await screenDrag(vc2, { x: vc2.x + 40, y: vc2.y + 25 }, "middle");
  p = await screenOf("#insp-image", front, centreBox(disc.bbox_mm));
  results.click = { zoom: await inspZoom() };
  await mouse("mouseMoved", p, 0);
  await mouse("mousePressed", p);
  await mouse("mouseReleased", p, 0);
  await waitFor(`document.querySelector('[data-testid="insp-title"]')?.innerText === "Floating material"`, "click on the disc");
  results.click.title = await text('[data-testid="insp-title"]');
  await showInspector();
  await shot("i3_click_after_zoom_pan");
  results.nav = { start: await text('[data-testid="insp-position"]') };
  await clickSel('[data-testid="insp-next"]');
  await sleep(300);
  results.nav.next = await text('[data-testid="insp-position"]');
  await clickSel('[data-testid="insp-prev"]');
  await sleep(300);
  results.nav.prev = await text('[data-testid="insp-position"]');

  // 5. Layer 2: only its own issue (the 0.5 mm slot), drawn on layer 2.
  await chip(layer2);
  const slot2 = find(layer2, "opening_may_close");
  results.layer2 = { shown: await text('[data-testid="insp-shown"]'), rows: await rows(), api: on(layer2).length };
  await selectRow("Opening may close");
  results.layer2.measured = await text('[data-testid="insp-measured"]');
  results.layer2.where = await text('[data-testid="insp-sheet"]');
  results.layer2.inside = await overlayAt(front, centreBox(slot2.bbox_mm));
  await showInspector();
  await shot("i4_layer2_opening"); await shotEl("layer2_opening", '[data-testid="issue-inspector"]');

  // 6. A layer without issues: said so, nothing drawn.
  const clean = data.sheets.find((s) => !on(s.id).length);
  await chip(clean.id);
  results.clean = { rows: await rows(), text: await text('[data-testid="insp-card-empty"]'), empty: await overlayEmpty(),
                    shown: await text('[data-testid="insp-shown"]') };
  await showInspector();
  await shot("i5_clean_layer"); await shotEl("clean_layer", '[data-testid="issue-inspector"]');

  // 7. Intake findings on the artwork (not on any layer): Edit artwork only.
  await chip("artwork");
  const intake = data.issues.filter((i) => i.kind === "intake_detail_changed");
  results.artwork = { rows: await rows(), api: data.issues.filter((i) => !i.sheet && i.destination === "artwork").length, intake: intake.length };
  await selectRow("Detail changed by intake");
  results.artwork.where = await text('[data-testid="insp-sheet"]');
  results.artwork.layer_buttons = await count('[data-testid="insp-edit-layer"]');
  results.artwork.artwork_buttons = await count('[data-testid="insp-edit-artwork"]');
  results.artwork.detail_image = await count('[data-testid="insp-detail-image"]');
  await showInspector();
  await shot("i6_intake_on_artwork"); await shotEl("intake_on_artwork", '[data-testid="issue-inspector"]');

  // 8. Edit layer from the layer 1 slot: the sheet editor opens on layer 1, zoomed and centred on the slot.
  await chip(layer1);
  await selectRow("Opening may close");
  await clickSel('[data-testid="insp-edit-layer"]');
  await waitImage("#edit-image");
  await sleep(600);
  const meta = data.sheets.find((s) => s.id === layer1);
  results.edit_layer = { image: await evaluate(`document.querySelector("#edit-image").src`), export_name: meta.export_name,
                         hash: await evaluate("location.hash"),
                         fill: await fillRatio("#edit-image", front, slot.bbox_mm, "#edit-viewport"),
                         centre_offset_px: dist(await screenOf("#edit-image", front, centreBox(slot.bbox_mm)), await centreOf("#edit-viewport")),
                         selected_layer: await evaluate(`document.querySelector('[data-testid="layer-row"].selected')?.innerText ?? ""`) };
  await shot("i7_edit_layer");

  // 9. A layer edit (undo the spike): the old locations are marked out of date, then re-checked.
  const before = (await issuesApi(projectSheets)).basis;
  await clickSel('[data-action="undo"]');
  let seenStale = false;
  const end = Date.now() + 120000;
  while (Date.now() < end) {
    const st = await evaluate(`Boolean(document.querySelector('[data-testid="insp-basis-stale"]'))`);
    if (st && !seenStale) { seenStale = true; await showInspector(); await shot("i8_stale_after_edit"); await shotEl("stale_after_edit", '[data-testid="issue-inspector"]'); }
    const fresh = await evaluate(`(() => { const t = document.querySelector('[data-testid="insp-summary"]'); return Boolean(t) && !document.querySelector('[data-testid="insp-basis-stale"]') && !document.querySelector('[data-testid="job-banner"]'); })()`);
    if (seenStale && fresh) break;
    await sleep(60);
  }
  const after = await issuesApi(projectSheets);
  results.stale = { seen: seenStale, basis_changed: after.basis !== before,
                    spike_after: after.issues.some((i) => i.kind === "thin_material" && i.bbox_mm[0] > 149 && i.bbox_mm[2] < 151),
                    spike_before: data.issues.some((i) => i.kind === "thin_material" && i.bbox_mm[0] > 149 && i.bbox_mm[2] < 151),
                    rows_now: await rows(), api_now: after.issues.filter((i) => i.sheet === layer1).length };
  await showInspector();
  await shot("i9_rechecked"); await shotEl("rechecked", '[data-testid="issue-inspector"]');

  // 10. A valid proposed candidate (no review): every layer checked and clear; intake warnings on the artwork only.
  const cand = await issuesApi(projectCandidate);
  await send("Page.navigate", { url: `${url}#/${projectCandidate}/proposal` });
  await waitFor(`location.hash.startsWith(${JSON.stringify(`#/${projectCandidate}`)}) && document.querySelector('[data-testid="insp-layer-chip"][data-key="${cand.sheets[0].id}"]') && ${idle}`, "candidate issues", 180000);
  await waitImage("#insp-image");
  await sleep(300);
  results.candidate = { chips: await chips(), shown: await text('[data-testid="insp-shown"]'), summary: await text('[data-testid="insp-summary"]'),
                        text: await text('[data-testid="insp-card-empty"]'), rows: await rows(), empty: await overlayEmpty(),
                        sheet_issues: cand.issues.filter((i) => i.sheet || i.destination === "layer").length,
                        intake: cand.issues.filter((i) => i.kind === "intake_detail_changed").length,
                        art_api: cand.issues.filter((i) => !i.sheet && i.destination === "artwork").length, kinds: cand.issues.map((i) => i.kind) };
  await showInspector();
  await shot("i10_candidate_valid_layers"); await shotEl("candidate_valid_layers", '[data-testid="issue-inspector"]');
  await chip("artwork");
  results.candidate.artwork_rows = await rows();
  await selectRow("Detail changed by intake");
  await showInspector();
  await shot("i11_candidate_intake_on_artwork"); await shotEl("candidate_intake_on_artwork", '[data-testid="issue-inspector"]');

  // 11. A failed proposal: artwork-level locations only.
  await send("Page.navigate", { url: `${url}#/${projectFailed}/proposal` });
  await waitFor(`!document.querySelector('[data-testid="insp-layer-chip"][data-key="${cand.sheets[0].id}"]')`, "candidate page left", 60000);
  await waitFor(`location.hash.startsWith(${JSON.stringify(`#/${projectFailed}`)}) && document.querySelector('[data-testid="insp-notice"]') && document.querySelectorAll('[data-testid="insp-row"]').length > 0 && ${idle}`, "failed proposal issues", 120000);
  const failedData = await issuesApi(projectFailed);
  results.failed = { notice: await text('[data-testid="insp-notice"]'), rows: await rows(), api: failedData.issues.length,
                     chips: (await chips()).map((c) => c.key) };
  await selectRow("Feature the planner cannot support");
  results.failed.layer_buttons = await count('[data-testid="insp-edit-layer"]');
  results.failed.shown = await text('[data-testid="insp-shown"]');
  await showInspector();
  await shot("i12_failed_proposal"); await shotEl("failed_proposal", '[data-testid="issue-inspector"]');

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
