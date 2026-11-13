// Drives a review of a rectangular (portrait 150 x 250 mm) sheet in a headless Chromium browser (Edge or
// Chrome) through the DevTools protocol: the Issue inspector (select an issue from the list and by a real
// click on its location; the highlight drawn on it, not beside it; its position in the artwork), the
// artwork editor's Fill tool by a real click, Save, and a reload.  No npm packages: Node >= 22.
// Same bootstrap and helpers as drive_issues.mjs.
//
//   node tests/e2e/drive_rect_review.mjs <app url> <browser exe> <unused> <screenshot dir> <project> <points json>
//
// The project (sheet cuts and a fill made through the edit API) is prepared by tests/test_l4_app.py, which
// also checks the masks.  Prints one JSON line.
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


const project = process.argv[6];
const points = JSON.parse(process.argv[7]);  // {sheet; slot_in, slot_out: sheet mm; fill_px, pick_px: [row, col]; color}
// (the position in the artwork is in the collapsed Technical details: its text, not its rendering)
const insp = async () => ({ title: await text('[data-testid="insp-title"]'),
                            aperture: await evaluate(`document.querySelector('[data-testid="insp-aperture-pos"]')?.textContent ?? ""`) });
const opaque = (px) => Array.isArray(px) && px[3] > 0;
try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/review` });
  await waitFor(`document.querySelectorAll('[data-testid="insp-row"]').length > 0 && ${idle}`, "issues listed", 180000);
  await waitImage("#insp-image");
  const data = await issuesApi(encodeURIComponent(project));
  const front = data.images.find((i) => i.key === "front").extent_mm;
  const slot = data.issues.find((i) => i.kind === "opening_may_close" && i.sheet === points.sheet);
  results.extent = front;
  results.grid = data.grid;
  results.issue = { id: slot?.id, bbox_mm: slot?.bbox_mm };
  await showInspector();

  // 1. Select the issue in the list: the highlight is on the slot and not 1.4 mm beside it.
  await selectRow("Opening may close");
  results.from_list = { ...(await insp()), inside: await overlayAt(front, points.slot_in), outside: await overlayAt(front, points.slot_out) };
  await shotEl("rect_insp_selected", '[data-testid="issue-inspector"]');
  step("selected from the list", results.from_list);

  // 2. Select it by a real click on the image where the slot is (another issue selected first, if any).
  await clickSel('[data-testid="insp-next"]');
  await sleep(300);
  results.before_click = await text('[data-testid="insp-title"]');
  const p = await screenOf("#insp-image", front, points.slot_in);
  await mouse("mouseMoved", p, 0);
  await mouse("mousePressed", p);
  await mouse("mouseReleased", p, 0);
  await waitFor(`document.querySelector('[data-testid="insp-title"]')?.innerText === "Opening may close"`, "click on the slot");
  results.from_click = { ...(await insp()), inside: await overlayAt(front, points.slot_in), outside: await overlayAt(front, points.slot_out) };
  step("selected by a click", results.from_click);

  // 3. The artwork editor's Fill tool, by a real click on an artwork pixel, then Save.
  await clickSel('.review-bar [data-testid="edit-artwork"]');  // folded in Review until opened (UX Phase 2)
  await waitFor(`document.querySelector('[data-testid="artwork-panel"]')?.open && document.querySelector("#art-image")?.naturalWidth > 0`, "artwork editor");
  results.art_image = await evaluate(`[document.querySelector("#art-image").naturalWidth, document.querySelector("#art-image").naturalHeight]`);
  await clickSel('[data-testid="art-mode-fill"]');
  await clickSel('[data-testid="art-eyedropper"]');  // the color: sampled by a real click on the hull
  await artClick(points.pick_px);
  await waitFor(`document.querySelector('[data-testid="art-chosen"]')?.innerText.includes(${JSON.stringify(points.color)})`, "color sampled");
  await clickSel('[data-testid="art-mode-fill"]');
  await artClick(points.fill_px);
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("1 unsaved change")`, "fill");
  await clickSel('[data-testid="art-save"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "") === "All changes saved" && ${idle}`, "saved");
  const artEdits = () => evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => s.artwork.edits.map((e) => e.op))`);
  results.saved_ops = await artEdits();
  step("artwork fill saved", { ops: results.saved_ops.length });

  // 4. Reload: the sheet edits, the artwork edit and the issue are still there.
  await evaluate("window.__beforeReload = true");
  await send("Page.reload");
  await waitFor(`!window.__beforeReload && document.querySelectorAll('[data-testid="insp-row"]').length > 0 && ${idle}`, "after reload", 180000);
  await waitImage("#insp-image");
  await showInspector();
  await selectRow("Opening may close");
  results.after_reload = { ...(await insp()), inside: await overlayAt(front, points.slot_in), outside: await overlayAt(front, points.slot_out),
                           history: await count('[data-testid="history-item"]'), ops: await artEdits() };
  await shot("rect_review_after_reload");
  results.opaque = { list: [opaque(results.from_list.inside), opaque(results.from_list.outside)],
                     click: [opaque(results.from_click.inside), opaque(results.from_click.outside)],
                     reload: [opaque(results.after_reload.inside), opaque(results.after_reload.outside)] };
  results.console_errors = consoleErrors;
  results.ok = true;
} catch (error) {
  results.ok = false;
  results.error = String(error.stack || error);
  try { await shot("error_state"); } catch {}
  results.console_errors = consoleErrors;
}
console.log(JSON.stringify(results));
try { ws.close(); } catch {}
browser.kill();
await sleep(500);
try { rmSync(profile, { recursive: true, force: true }); } catch {}
process.exit(results.ok ? 0 : 1);
