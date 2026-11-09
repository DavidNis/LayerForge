// Drives Simplify tiny tips in the Artwork editor in a headless Chromium browser (Edge or Chrome) through
// the DevTools protocol: the suggestion card (tips listed and numbered on the artwork), After / Before /
// Changes views, Show (zoom to a tip), leaving a tip out, Apply as one edit, then Undo / Redo / Save. No npm
// packages: Node >= 22 (built-in WebSocket). Same bootstrap as drive_review.mjs.
//
//   node tests/e2e/drive_artwork_tips.mjs <app url> <browser exe> <unused> <screenshot dir> <project> [tab]
//
// The project is prepared by tests/test_simplify_tips.py. Prints one JSON line.
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
const [, , , , project, tab = "proposal"] = process.argv.slice(2);
const img = "#art-image";
const unsaved = () => text('[data-testid="art-unsaved"]');
const summary = () => text('[data-testid="art-tips-summary"]');
// Pixels of the overlay canvas in the exact "changed pixel" magenta (#FF00C8); box outlines are darker.
const magenta = () => evaluate(`(() => { const c = document.querySelector("#art-overlay"); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] > 240 && d[i + 1] < 20 && d[i + 2] > 185 && d[i + 2] < 215 && d[i + 3] > 240) n++; return n; })()`);
const imageSrc = () => evaluate(`document.querySelector("${img}").src.replace(location.origin, "").split("?")[0]`);
const settledImage = () => waitFor(`document.querySelector("${img}").complete && !document.querySelector('[data-testid="art-pending"]')`, "image shown");

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/${tab}` });
  await waitFor(`document.querySelector('[data-testid="artwork-panel"]') && ${idle}`, "artwork panel", 300000);
  await waitFor(`document.querySelector("${img}")?.naturalWidth > 0`, "artwork image");
  await evaluate(`(() => { const p = document.querySelector('[data-testid="artwork-panel"]'); if (!p.open) p.querySelector("summary").click(); })()`);
  await evaluate(`document.querySelector('[data-testid="artwork-panel"]').scrollIntoView({block: "start"})`);
  const t0 = await evaluate("performance.now()");
  await clickSel('[data-testid="art-tips-open"]');
  await waitFor(`document.querySelector('[data-testid="art-tips-summary"]') && !document.querySelector('[data-testid="art-pending"]')`, "suggestion", 120000);
  await settledImage();
  results.open_ms = Math.round((await evaluate("performance.now()")) - t0);
  results.opened = { summary: await summary(), rows: await count('[data-testid="art-tip"]'),
    kept: await text('[data-testid="art-tips-kept"] summary'), image: await imageSrc() };
  await shot("tips_card");
  step("suggestion shown", results.opened);

  // After / Before / Changes.
  results.views = { after: await imageSrc(), after_magenta: await magenta() };
  await clickSel('[data-testid="art-tips-view-before"]');
  await settledImage();
  results.views.before = await imageSrc();
  await clickSel('[data-testid="art-tips-view-changes"]');
  await settledImage();
  await sleep(150);
  results.views.changes_magenta = await magenta();
  await shot("tips_changes_view");

  // Show: the view zooms to the tip.
  const zoomBefore = await text('[data-testid="art-zoom-label"]');
  await clickSel('[data-testid="art-tip-show"]');
  await waitFor(`document.querySelector('[data-testid="art-zoom-label"]').innerText !== ${JSON.stringify(zoomBefore)}`, "zoomed to the tip");
  results.show = { zoom_before: zoomBefore, zoom: await text('[data-testid="art-zoom-label"]') };
  await shot("tips_show_tip");
  await clickSel('[data-testid="art-zoom-fit"]');

  // Leave the second tip out.
  await clickSel('[data-testid="art-tips-view-after"]');
  await evaluate(`document.querySelectorAll('[data-testid="art-tip-check"]')[1].click()`);
  const n = results.opened.rows;
  await waitFor(`(document.querySelector('[data-testid="art-tips-summary"]')?.innerText || "").startsWith("${n - 1} of ${n}") && !document.querySelector('[data-testid="art-pending"]')`, "tip left out");
  await settledImage();
  results.excluded = { summary: await summary(), apply: await text('[data-testid="art-tips-apply"]') };
  const art = await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => s.artwork)`);
  results.artwork_untouched_before_apply = !art.unsaved && art.edits.length === 0;

  // Apply: one unsaved edit; then the usual Undo / Redo / Save.
  const t1 = await evaluate("performance.now()");
  await clickSel('[data-testid="art-tips-apply"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("1 unsaved") && !document.querySelector('[data-testid="art-pending"]') && document.querySelector("${img}").complete`, "applied");
  results.apply_ms = Math.round((await evaluate("performance.now()")) - t1);
  results.applied = { unsaved: await unsaved(), history: await text('[data-testid="art-history-unsaved"]'),
    card_open: !!(await evaluate(`document.querySelector('[data-testid="art-tips"]')`)) };
  await shot("tips_applied");
  await clickSel('[data-testid="art-session-undo"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "") === "All changes saved" && !document.querySelector('[data-testid="art-pending"]')`, "undone");
  results.undo = await unsaved();
  await clickSel('[data-testid="art-session-redo"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("1 unsaved") && !document.querySelector('[data-testid="art-pending"]')`, "redone");
  results.redo = await unsaved();
  await clickSel('[data-testid="art-save"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "") === "All changes saved" && !document.querySelector('[data-testid="art-pending"]')`, "saved");
  results.saved = await unsaved();
  // Reopened after the saved edit: the narrow cutouts are from the sheets planned before it.
  await clickSel('[data-testid="art-tips-open"]');
  await waitFor(`(document.querySelector('[data-testid="art-tips-summary"]') || document.querySelector('[data-testid="art-tips-status"]')) && !document.querySelector('[data-testid="art-pending"]')`, "reopened", 120000);
  results.reopened = { stale: await text('[data-testid="art-tips-stale"]'), summary: await text('[data-testid="art-tips-summary"]'),
    edited_rows: await evaluate(`[...document.querySelectorAll('[data-testid="art-tip"]')].filter((li) => li.innerText.includes("edited here since planning")).length`) };
  await shot("tips_reopened_stale");
  await clickSel('[data-testid="art-tips-close"]');
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
