// Drives the Artwork editor while the Issues panel above it loads and refreshes, in a headless Chromium
// browser (Edge or Chrome) through the DevTools protocol. Every /issues answer is held with the Fetch
// domain and released at a chosen moment: with the pointer down in the middle of a stroke (first load;
// then the refresh after an edit, after zooming, panning and scrolling the page). Each drag's screen points
// are measured once before it starts, so a moved canvas would put the stroke on other pixels; the saved
// points must be exactly the intended ones. Then two more edits in sequence, and Save. No npm packages:
// Node >= 22 (built-in WebSocket). Same bootstrap as drive_review.mjs.
//
//   node tests/e2e/drive_artwork_stable.mjs <app url> <browser exe> <unused> <screenshot dir> <project>
//
// The project (imported without planning) is prepared by tests/test_artwork_responsive.py. Prints one JSON line.
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
const paused = [];  // requests held by the Fetch domain (see release / fail)
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, fail } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result);
  } else if (msg.method === "Page.javascriptDialogOpening") {  // confirm() / beforeunload: recorded, then answered
    dialogs.push({ type: msg.params.type, message: msg.params.message });
    send("Page.handleJavaScriptDialog", { accept: dialogAccept });
  } else if (msg.method === "Fetch.requestPaused") {
    paused.push({ requestId: msg.params.requestId, url: msg.params.request.url });
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
const [, , , , project] = process.argv.slice(2);
const img = "#art-image";
const LINES = {
  A: [[240, 200], [240, 330]],   // drawn while the first Issues load finishes (pointer held down)
  B: [[250, 220], [250, 300]],   // after zoom, pan and page scroll, while the refresh finishes (pointer down)
  C: [[270, 220], [270, 300]],   // sequential edits
  D: [[230, 220], [230, 300]],
};
async function nextPaused(what, timeoutMs = 60000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (paused.length) return paused.shift();
    await sleep(50);
  }
  throw new Error(`no held request: ${what}`);
}
const release = (req) => send("Fetch.continueRequest", { requestId: req.requestId });
const vpTop = () => evaluate(`document.querySelector("#art-viewport").getBoundingClientRect().top`);
const inspHeight = () => evaluate(`document.querySelector('[data-testid="issue-inspector"]')?.getBoundingClientRect().height || 0`);
const view = () => evaluate(`({ zoom: document.querySelector('[data-testid="art-zoom-label"]')?.innerText, stage: document.querySelector("#art-stage").style.transform, scrollY: Math.round(scrollY) })`);
const issuesDone = () => evaluate(`window.__issuesDone || 0`);
// Screen points of artwork pixels, measured once (no scrolling): if the canvas moved during the drag,
// the stroke would end on other pixels and the saved points would show it.
const screenOf = (px) => artPoint(px, false);
async function stroke(line, during = null) {
  const a = await screenOf(line[0]);
  const b = await screenOf(line[1]);
  await mouse("mouseMoved", a, 0);
  await mouse("mousePressed", a);
  for (let i = 1; i <= 3; i++) await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / 8, y: a.y + ((b.y - a.y) * i) / 8 });
  const seen = during ? await during() : null;  // e.g. the Issues answer arrives while the pointer is down
  for (let i = 4; i <= 8; i++) await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / 8, y: a.y + ((b.y - a.y) * i) / 8 });
  await mouse("mouseReleased", b, 0);
  return seen;
}
const unsaved = (n) => `(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("${n} unsaved change")`;
async function apply(n, line) {  // preview shown at the intended points, then Done
  const want = `(${line[0][0]}, ${line[0][1]}) → (${line[1][0]}, ${line[1][1]})`;
  await waitFor(`document.querySelector("${img}").src.includes("/artwork/preview/after.png") && document.querySelector('[data-testid="art-stroke-info"]') && !document.querySelector('[data-testid="art-pending"]')`, `preview ${n}`);
  const points = await text('[data-testid="art-shape-points"]');
  await clickSel('[data-testid="art-done"]');
  await waitFor(`${unsaved(n)} && !document.querySelector('[data-testid="art-pending"]') && !document.querySelector('[data-testid="art-shape"]')`, `applied ${n}`);
  return { points, intended: want };
}
// Release a held Issues answer and wait (pointer still down) until it has been shown: the panel element
// is replaced (new content), then two frames for layout.
async function issuesArrive(req, before) {
  await evaluate(`document.querySelector('[data-testid="issue-inspector"]').__old = true`);
  await release(req);
  await waitFor(`(window.__issuesDone || 0) > ${before} && !document.querySelector('[data-testid="issue-inspector"]').__old && document.querySelector('[data-testid="issue-inspector"]').tagName === "DETAILS"`, "issues shown");
  await evaluate(`new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)))`);
}

try {
  // Count finished Issues requests (the panel is shown right after) and hold every /issues answer.
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `const f = window.fetch; window.fetch = async (...a) => {
    try { return await f(...a); } finally { if (/\\/issues$/.test(String(a[0]))) window.__issuesDone = (window.__issuesDone || 0) + 1; } };` });
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/projects/*/issues", requestStage: "Request" }] });
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/proposal` });
  await waitFor(`document.querySelector('[data-testid="artwork-panel"]') && ${idle}`, "artwork panel", 300000);
  await waitFor(`document.querySelector("${img}")?.naturalWidth > 0`, "artwork image");
  const firstIssues = await nextPaused("first Issues load");
  results.loading_placeholder = await evaluate(`document.querySelector('[data-testid="issue-inspector"]')?.innerText || ""`);
  await evaluate(`document.querySelector("#art-viewport").scrollIntoView({block: "center"})`);
  await sleep(200);
  await clickSel('[data-testid="art-mode-line"]');
  await evaluate(`(() => { const i = document.querySelector("#art-width"); i.value = "2"; i.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  await clickSel('[data-testid="art-palette-color"][data-color="#774627"]');
  await waitFor(`document.querySelector('[data-testid="art-chosen"]')?.innerText.includes("#774627")`, "color chosen");

  // 1. Line A while Issues is still loading; the answer arrives with the pointer down.
  const a0 = { top: await vpTop(), insp: await inspHeight(), view: await view() };
  const doneBefore = await issuesDone();
  results.load = await stroke(LINES.A, async () => {
    await issuesArrive(firstIssues, doneBefore);
    return { top_before: a0.top, top_after: await vpTop(), insp_before: a0.insp, insp_after: await inspHeight(), view_before: a0.view, view_after: await view() };
  });
  results.A = await apply(1, LINES.A);
  step("line A while Issues loaded");

  // 2. The refresh after the edit is held. Meanwhile: zoom (wheel), pan (middle drag), scroll the page.
  const refresh = await nextPaused("Issues refresh after line A");
  const centre = await evaluate(`(() => { const b = document.querySelector("#art-overlay").getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; })()`);
  await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: centre.x, y: centre.y, deltaX: 0, deltaY: -240 });
  await waitFor(`document.querySelector('[data-testid="art-zoom-label"]')?.innerText !== "fit"`, "zoomed");
  await mouse("mouseMoved", centre, 0);
  await mouse("mousePressed", centre, 4, "middle");
  for (let i = 1; i <= 4; i++) await mouse("mouseMoved", { x: centre.x + 8 * i, y: centre.y + 5 * i }, 4, "middle");
  await mouse("mouseReleased", { x: centre.x + 32, y: centre.y + 20 }, 0, "middle");
  await evaluate(`window.scrollBy(0, 40)`);
  await sleep(150);
  const b0 = { top: await vpTop(), insp: await inspHeight(), view: await view() };
  const doneBefore2 = await issuesDone();
  results.refresh = await stroke(LINES.B, async () => {
    await issuesArrive(refresh, doneBefore2);
    return { top_before: b0.top, top_after: await vpTop(), insp_before: b0.insp, insp_after: await inspHeight(), view_before: b0.view, view_after: await view() };
  });
  results.B = await apply(2, LINES.B);
  step("line B during zoom, pan, scroll and the refresh");

  // 3. Sequential edits; their refreshes pass straight through.
  while (paused.length) await release(paused.shift());
  await send("Fetch.disable");
  const c0 = await vpTop();
  await stroke(LINES.C);
  results.C = await apply(3, LINES.C);
  await stroke(LINES.D);
  results.D = await apply(4, LINES.D);
  results.sequential_top = { before: c0, after: await vpTop() };
  step("lines C and D");

  await clickSel('[data-testid="art-save"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "") === "All changes saved" && !document.querySelector('[data-testid="art-pending"]')`, "saved");
  results.saved_points = await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => s.artwork.edits.map((e) => e.op.points_px))`);
  results.final_view = await view();
  await shot("stable_after_save");
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
