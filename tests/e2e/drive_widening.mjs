// Drives the Cutout widening summary on Review Layers in a headless Chromium browser (Edge or Chrome)
// through the DevTools protocol: outcome facts, validation vs remaining print risks, changed layers by
// number, the Issues link, technical details (collapsed), narrow layout and Undo.  No npm packages:
// Node >= 22 (built-in WebSocket).  Same bootstrap as drive_review.mjs.
//
//   node tests/e2e/drive_widening.mjs <app url> <browser exe> <unused> <screenshot dir> <project> [keep]
//
// The project (a review whose first edit is the widening) is prepared through the API by
// tests/test_widening.py; "keep" skips the final Undo.  Prints one JSON line.
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
const [, , , , project, mode] = process.argv.slice(2);
const visibleText = (sel) => evaluate(`(document.querySelector(${JSON.stringify(sel)})?.innerText || "").replace(/\\s+/g, " ").trim()`);
const panel = '[data-testid="widening-panel"]';
// Panel text without the Technical details section (what the user reads first).
const mainText = () => evaluate(`(() => { const p = document.querySelector('${panel}').cloneNode(true); p.querySelector('[data-testid="widen-tech"]')?.remove(); return p.innerText.replace(/\\s+/g, " ").trim(); })()`);

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/review` });
  await waitFor(`document.querySelectorAll('[data-testid="layer-row"]').length > 1 && ${idle} && document.querySelector('${panel}')`, "review", 300000);
  const state = await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => ({ widening: s.draft.widening, edits: s.draft.edits, sheets: s.draft.sheets.map((x) => x.id), mpp: s.draft.validation.mm_per_pixel }))`);
  results.state = state;
  results.collapsed = { open: await evaluate(`document.querySelector('${panel}').open`), headline: await visibleText('[data-testid="widen-headline"]') };
  await clickSel(`${panel} > summary`);
  await waitFor(`document.querySelector('${panel}').open`, "panel open");
  if (mode === "revert") {  // widening followed by later edits: Revert asks first; Cancel keeps every edit
    const edits = state.edits.length;
    const keep = state.widening.last.index - 1;
    const history = `document.querySelectorAll('[data-testid="history-item"]').length`;
    results.revert = { label: await visibleText('[data-testid="widen-undo"]'), edits_before: edits };
    dialogAccept = false;
    await clickSel('[data-testid="widen-undo"]');
    for (let i = 0; i < 50 && !dialogs.length; i++) await sleep(100);
    await sleep(1500);  // a revert, had it been sent, would start a job
    results.revert.cancel = { dialogs: dialogs.slice(), history: await evaluate(history), busy: !(await evaluate(idle)),
      api_edits: await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => s.draft.edits.length)`) };
    dialogAccept = true;
    await clickSel('[data-testid="widen-undo"]');
    await waitFor(`${idle} && ${history} === ${keep}`, "reverted", 300000);
    results.revert.accept = { dialogs: dialogs.length, history: await evaluate(history), headline: await visibleText('[data-testid="widen-headline"]') };
    throw "done";
  }
  results.panel = {
    what: await visibleText('[data-testid="widen-what"]'),
    facts: await evaluate(`Object.fromEntries([...document.querySelectorAll('[data-testid="widen-facts"] > div')].map((d) => [d.dataset.testid, d.innerText.replace(/\\s+/g, " ").trim()]))`),
    validation: await visibleText('[data-testid="widen-validation"]'),
    validation_class: await evaluate(`document.querySelector('[data-testid="widen-validation"]')?.className || ""`),
    remaining: await visibleText('[data-testid="widen-remaining"]'),
    layers: await evaluate(`[...document.querySelectorAll('[data-testid="widen-layer"]')].map((b) => ({ sheet: b.dataset.sheet || null, text: b.innerText.replace(/\\s+/g, " ").trim() }))`),
    main_text: await mainText(),
    tech_open: await evaluate(`document.querySelector('[data-testid="widen-tech"]').open`),
    badges: await evaluate(`document.querySelectorAll('${panel} .badge').length`),
    undo: await visibleText('[data-testid="widen-undo"]'),
    show_changes: await evaluate(`Boolean(document.querySelector('[data-testid="widen-show-changes"]'))`),
    open_issues: await evaluate(`Boolean(document.querySelector('[data-testid="widen-open-issues"]'))`),
    change_panel: await evaluate(`document.querySelectorAll('[data-testid="change-panel"]').length`),
    height: await evaluate(`Math.round(document.querySelector('${panel}').getBoundingClientRect().height)`),
  };
  await clickSel('[data-testid="widen-tech"] > summary');
  await waitFor(`document.querySelector('[data-testid="widen-tech"]').open`, "tech open");
  results.panel.tech_text = await visibleText('[data-testid="widen-tech"]');
  await shotEl("widening_panel", panel);
  await clickSel('[data-testid="widen-tech"] > summary');
  step("panel", { facts: results.panel.facts, layers: results.panel.layers.length });

  // A changed layer: selected in the layer list and shown alone.
  const first = results.panel.layers.find((l) => l.sheet);
  if (first) {
    await clickSel(`[data-testid="widen-layer"][data-sheet="${first.sheet}"]`);
    await waitFor(`document.querySelector('[data-testid="layer-row"].selected')?.dataset.sheet === ${JSON.stringify(first.sheet)}`, "layer selected");
    results.layer_click = { selected: first.sheet, view: await visibleText('[data-testid="view-switch"] .active'),
                            panel_open: await evaluate(`document.querySelector('${panel}').open`),
                            chip_active: await evaluate(`document.querySelector('[data-testid="widen-layer"][data-sheet="${first.sheet}"]').classList.contains("active")`) };
  }
  if (results.panel.show_changes) {
    await clickSel('[data-testid="widen-show-changes"]');
    await sleep(200);
    results.show_changes = await visibleText('[data-testid="view-switch"] .active');
  }
  if (results.panel.open_issues) {
    await waitFor(`document.querySelector('[data-testid="issue-inspector"]')?.tagName === "DETAILS"`, "issues loaded", 300000);
    await evaluate(`document.querySelector('[data-testid="issue-inspector"]').open = false`);
    await clickSel('[data-testid="widen-open-issues"]');
    await sleep(800);
    results.issues = { open: await evaluate(`document.querySelector('[data-testid="issue-inspector"]').open`),
                       in_view: await evaluate(`(() => { const r = document.querySelector('[data-testid="issue-inspector"]').getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; })()`) };
  }
  // Narrow window: no horizontal overflow; the facts wrap to two columns.
  await send("Emulation.setDeviceMetricsOverride", { width: 700, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  results.narrow = await evaluate(`(() => { const p = document.querySelector('${panel}'); const f = document.querySelector('[data-testid="widen-facts"]');
    return { overflow: p.scrollWidth > p.clientWidth + 1, columns: getComputedStyle(f).gridTemplateColumns.split(" ").length }; })()`);
  await shotEl("widening_panel_narrow", panel);
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(300);

  if (mode !== "keep") {  // Undo widening: back to the planned layers through the existing undo
    const edits = results.state.edits.length;
    await clickSel('[data-testid="widen-undo"]');
    await waitFor(`${idle} && document.querySelectorAll('[data-testid="history-item"]').length === ${edits - 1}`, "undone", 300000);
    await waitFor(`document.querySelector('[data-testid="widen-headline"]')`, "panel after undo");
    results.after_undo = { headline: await visibleText('[data-testid="widen-headline"]'), run: await evaluate(`!document.querySelector('[data-testid="widen-run"]').disabled`) };
  }
  results.console_errors = consoleErrors;
  results.ok = true;
} catch (error) {
  results.ok = error === "done";  // "done": the revert-mode drive ends early
  if (!results.ok) results.error = String(error.stack || error);
  results.console_errors = consoleErrors;
}

console.log(JSON.stringify(results));
try { ws.close(); } catch {}
browser.kill();
await sleep(500);
try { rmSync(profile, { recursive: true, force: true }); } catch {}
process.exit(results.ok ? 0 : 1);
