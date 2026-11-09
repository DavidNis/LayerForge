// Drives the Review Layers screen of the real LayerForge app in a headless Chromium browser (Edge or
// Chrome) through the DevTools protocol: layer selection, views, wheel zoom, drag pan, issue navigation
// and the separate Artwork tab.  No npm packages: Node >= 22 (built-in WebSocket).  Same bootstrap as
// drive_issues.mjs.
//
//   node tests/e2e/drive_review.mjs <app url> <browser exe> <unused> <screenshot dir> <reviewed project> [before]
//
// The project (a review with earlier sheet edits) is prepared through the API by tests/test_review_layers.py.
// Prints one JSON line.

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
const [, , , , project, mode] = process.argv.slice(2);  // mode "before": screenshots only (the earlier UI)
const editTransform = () => evaluate(`document.querySelector("#edit-stage").style.transform`);
const editZoom = () => text('[data-testid="edit-zoom-label"]');
const imageSrc = () => evaluate(`document.querySelector("#edit-image")?.getAttribute("src") || ""`);
const rect = (sel) => evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const b = e.getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height}; })()`);
const visibleText = (sel) => evaluate(`(document.querySelector(${JSON.stringify(sel)})?.innerText || "").replace(/\\s+/g, " ").trim()`);
// Every control that edits sheet geometry in the earlier Review screen, by selector.
const EDIT_CONTROLS = ['[data-action="tool"]', '[data-tool]', '.toolbar', '[data-action="apply-pending"]', '[data-action="merge"]',
  '#p-merge', '[data-action="raw-op"]', '#raw-op', '#bar-width', '#precision', '[data-testid="pending-edit"]',
  '[data-action="propose-op"][data-op*="remove_island"]', '[data-action="propose-op"][data-op*="split"]', '[data-action="propose-op"][data-op*="fill_hole"]'];
const editControls = () => evaluate(`Object.fromEntries(${JSON.stringify(EDIT_CONTROLS)}.map((s) => [s, document.querySelectorAll('#tab-body ' + s).length]).filter(([, n]) => n))`);
const sheetName = () => evaluate(`document.querySelector('[data-testid="sheet-panel"] h3')?.innerText || ""`);
async function viewportCentre() {
  const r = await rect("#edit-viewport");
  return { x: r.x + r.w / 2, y: r.y + r.h / 2 };
}
async function wheel(p, deltaY) {
  await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: p.x, y: p.y, deltaX: 0, deltaY });
}

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/review` });
  await waitFor(`document.querySelectorAll('[data-testid="layer-row"]').length > 1 && ${idle}`, "review", 120000);
  await waitFor(`document.querySelector("#edit-image")?.complete && document.querySelector("#edit-image").naturalWidth > 0`, "layer image");
  await waitFor(`document.querySelector('[data-testid="insp-row"], [data-testid="insp-card-empty"]')`, "issues", 120000);
  await sleep(500);
  await shot("review_full");
  await send("Page.captureScreenshot", { format: "png" }).then(({ data }) => writeFileSync(join(shotDir, "review_viewport.png"), Buffer.from(data, "base64")));
  results.before_layout = {
    viewport: await rect("#edit-viewport"), page_height: await evaluate("document.documentElement.scrollHeight"),
    edit_controls: await editControls(), artwork_panel: await count('#tab-body [data-testid="artwork-panel"]'),
    headings: await evaluate(`[...document.querySelectorAll("#tab-body h3, #tab-body h4")].map((h) => h.innerText.trim()).filter(Boolean)`),
    buttons: await evaluate(`[...document.querySelectorAll("#tab-body button")].filter((b) => b.checkVisibility()).length`),
    words: await evaluate(`document.querySelector("#tab-body").innerText.split(/\\s+/).length`),
  };
  step("loaded", results.before_layout);
  if (mode === "before") throw Object.assign(new Error("before"), { done: true });

  // 1. Read-only: no sheet geometry editing control anywhere on the screen; the artwork editor is its own tab.
  results.layout = results.before_layout;
  // 2. Layer selection: the second row from the front is shown alone, named in the layer panel.
  const rows = await evaluate(`[...document.querySelectorAll('[data-testid="layer-row"]')].map((r) => ({id: r.dataset.sheet, name: r.querySelector(".name").innerText}))`);
  await clickSel(`[data-testid="layer-row"][data-sheet="${rows[1].id}"]`);
  await waitFor(`document.querySelector('[data-testid="layer-row"].selected')?.dataset.sheet === ${JSON.stringify(rows[1].id)}`, "row selected");
  await waitFor(`document.querySelector("#edit-image").complete`, "layer image");
  results.select = { rows, selected: await evaluate(`document.querySelector('[data-testid="layer-row"].selected').dataset.sheet`),
                     panel: await sheetName(), src: await imageSrc(), view: await visibleText('[data-testid="view-switch"] .active'),
                     sheet_image: await evaluate(`fetch("/api/projects/${project}").then((r) => r.json()).then((s) => s.draft.sheets.find((x) => x.id === ${JSON.stringify(rows[1].id)}).image)`) };
  await shot("review_layer_selected");
  // Keyboard: ArrowDown moves to the next layer (toward the back).
  await evaluate(`document.querySelector('[data-testid="layer-row"].selected').focus()`);
  await key("keyDown", "ArrowDown", "ArrowDown", 40);
  await key("keyUp", "ArrowDown", "ArrowDown", 40);
  await sleep(200);
  results.select.after_arrow = await evaluate(`document.querySelector('[data-testid="layer-row"].selected')?.dataset.sheet`);
  step("layer selected", results.select);

  // 3. Assembled view, then zoom (wheel toward the centre) and pan (plain left drag once zoomed).
  await clickSel('[data-testid="view-switch"] [data-view="front"]');
  await waitFor(`document.querySelector("#edit-image").complete`, "assembled image");
  results.views = { front_src: await imageSrc(), labels: await evaluate(`[...document.querySelectorAll('[data-testid="view-switch"] button')].map((b) => b.innerText.trim())`) };
  await evaluate(`document.querySelector("#edit-viewport").scrollIntoView({block: "center"})`);
  await sleep(200);
  const c = await viewportCentre();
  const z0 = await editZoom();
  await wheel(c, -400);
  await sleep(150);
  const z1 = await editZoom();
  const t1 = await editTransform();
  await screenDrag({ x: c.x, y: c.y }, { x: c.x + 80, y: c.y + 50 });
  await sleep(150);
  const t2 = await editTransform();
  results.zoom = { before: z0, after: z1, transform_zoomed: t1, transform_panned: t2, marker_after_pan: await evaluate(`Boolean(window.__lfMarker)`) };
  await clickSel('[data-testid="edit-zoom-fit"]');
  await sleep(100);
  results.zoom.fit = await editZoom();
  step("zoom and pan", results.zoom);

  // 4. Issues: select one, step to the next, and show it on its layer in the large view (no editing).
  await clickSel('[data-testid="insp-row"]');
  await waitFor(`document.querySelector('[data-testid="insp-card"]')`, "issue card");
  const first = await text('[data-testid="insp-title"]');
  await clickSel('[data-testid="insp-next"]');
  await sleep(150);
  const second = await text('[data-testid="insp-title"]');
  results.issues = { first, second, card_buttons: await evaluate(`[...document.querySelectorAll('[data-testid="insp-card"] button')].map((b) => b.innerText.trim())`),
                     sheet: await text('[data-testid="insp-sheet"]') };
  await shotEl("issue_card", '[data-testid="issue-inspector"]');
  await clickSel('[data-testid="insp-edit-layer"]');
  await sleep(600);
  results.issues.after_show = { view: await visibleText('[data-testid="view-switch"] .active'), zoom: await editZoom(),
                                selected: await evaluate(`document.querySelector('[data-testid="layer-row"].selected')?.dataset.sheet`),
                                edit_controls: await editControls() };
  await shot("review_issue_shown");
  step("issues", results.issues);

  // 5. The Artwork editor stays in Review Layers, directly below the issues; the "Edit artwork" step scrolls to it.
  await evaluate("scrollTo(0, 0)");
  await clickSel('[data-step="artwork"]');
  await sleep(800);
  results.artwork = {
    hash: await evaluate("location.hash"), panel: await count('#tab-body [data-testid="artwork-panel"]'),
    after_issues: await evaluate(`Boolean(document.querySelector('[data-testid="issue-inspector"]').compareDocumentPosition(document.querySelector('[data-testid="artwork-panel"]')) & Node.DOCUMENT_POSITION_FOLLOWING) && document.querySelector('[data-testid="issue-inspector"]').nextElementSibling === document.querySelector('[data-testid="artwork-panel"]')`),
    in_view: await evaluate(`(() => { const r = document.querySelector('[data-testid="artwork-panel"]').getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; })()`),
  };
  await shotEl("artwork_editor", '[data-testid="artwork-panel"]');
  step("artwork editor", results.artwork);

  // 6. Layer settings (kept, collapsed) and the legacy edit history with undo.
  results.settings = { open: await evaluate(`document.querySelector('[data-section="layer-settings"]')?.open ?? null`),
                       fields: await evaluate(`[...document.querySelectorAll('[data-section="layer-settings"] [data-action]')].map((b) => b.dataset.action)`),
                       history: await evaluate(`document.querySelectorAll('[data-testid="history-item"]').length`),
                       undo: await evaluate(`!document.querySelector('[data-action="undo"]')?.disabled`) };
  step("settings", results.settings);
  results.console_errors = consoleErrors;
  results.ok = true;
} catch (error) {
  results.ok = Boolean(error.done);
  if (!error.done) results.error = String(error.stack || error);
  results.console_errors = consoleErrors;
}

console.log(JSON.stringify(results));
try { ws.close(); } catch {}
browser.kill();
await sleep(500);
try { rmSync(profile, { recursive: true, force: true }); } catch {}
process.exit(results.ok ? 0 : 1);
