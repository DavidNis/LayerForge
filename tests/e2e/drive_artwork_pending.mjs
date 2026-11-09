// Drives the Artwork editor's feedback while a request runs, in a headless Chromium browser (Edge or
// Chrome) through the DevTools protocol. The server's answers to line previews and edits are held or
// failed with the Fetch domain, so each state is observed deterministically: the drawn line shown at once
// (local, translucent) and marked in progress, no second submission meanwhile, the applied edit, a failed
// edit (shape kept and previewed again), a failed preview (no stroke left), then Save. No npm packages:
// Node >= 22 (built-in WebSocket). Same bootstrap as drive_review.mjs.
//
//   node tests/e2e/drive_artwork_pending.mjs <app url> <browser exe> <unused> <screenshot dir> <project>
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
const LINE_A = [[240, 200], [240, 330]];
const LINE_B = [[270, 200], [270, 330]];
const LINE_C = [[300, 200], [300, 330]];
// Held requests: answered by release() (passed on to the server) or fail() (an injected error).
async function nextPaused(what, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (paused.length) return paused.shift();
    await sleep(50);
  }
  throw new Error(`no held request: ${what}`);
}
const release = (req) => send("Fetch.continueRequest", { requestId: req.requestId });
const fail = (req) => send("Fetch.fulfillRequest", { requestId: req.requestId, responseCode: 400,
  responseHeaders: [{ name: "Content-Type", value: "application/json" }],
  body: Buffer.from(JSON.stringify({ error: { message: "injected failure" } })).toString("base64") });
// Is the local preview stroke (paint color, translucent) on the overlay around the middle of ``line``?
// The pending shape's own centre line is blue and the handles sit at the ends, so they do not count.
const ghostAt = (line) => evaluate(`(() => {
  const i = document.querySelector("${img}"), c = document.querySelector("#art-overlay");
  if (!i || !c) return 0;
  const r = i.getBoundingClientRect(), k = c.getBoundingClientRect(), dpr = c.width / k.width;
  const row = ${(line[0][0] + line[1][0]) / 2} + 0.5, col = ${(line[0][1] + line[1][1]) / 2} + 0.5;
  const x = Math.round((r.left + (col / i.naturalWidth) * r.width - k.left) * dpr), y = Math.round((r.top + (row / i.naturalHeight) * r.height - k.top) * dpr);
  const d = c.getContext("2d").getImageData(x - 6, y - 6, 13, 13).data;
  let best = 0;
  for (let p = 0; p < d.length; p += 4) if (d[p + 3] > 0 && d[p] > d[p + 2] + 20) best = Math.max(best, d[p + 3]);
  return best;
})()`);
const snap = (line) => evaluate(`(() => {
  const q = (s) => document.querySelector(s);
  return { pending: q('[data-testid="art-pending"]')?.innerText || null, image: q("${img}").src.replace(location.origin, ""),
    unsaved: q('[data-testid="art-unsaved"]')?.innerText || "", last: q('[data-testid="art-last"]')?.innerText || "",
    ok_mark: !!q('[data-testid="art-savebar"] .yes'), done_disabled: !!q('[data-testid="art-done"]')?.disabled,
    save_disabled: !!q('[data-testid="art-save"]')?.disabled, zoom: q('[data-testid="art-zoom-label"]')?.innerText,
    info: q('[data-testid="art-stroke-info"]')?.innerText || "", shape: !!q('[data-testid="art-shape"]') };
})()`).then(async (s) => ({ ...s, ghost_alpha: await ghostAt(line) }));
const previewShown = `document.querySelector("${img}").src.includes("/artwork/preview/after.png") && document.querySelector("${img}").complete && document.querySelector('[data-testid="art-stroke-info"]') && !document.querySelector('[data-testid="art-pending"]')`;
const drag = (line) => artDrag(...line);  // drawing may start while the Issues panel above still loads
const enter = async () => {
  await evaluate(`document.querySelector("#art-viewport").focus()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
};

try {
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `window.__log = []; const T = () => Math.round(performance.now());
    for (const t of ["pointerdown", "pointerup"]) document.addEventListener(t, (e) => window.__log.push([T(), t, e.target.id || e.target.tagName, Math.round(e.clientX), Math.round(e.clientY)]), true);
    document.addEventListener("DOMContentLoaded", () => new MutationObserver(() => window.__log.push([T(), "render"])).observe(document.querySelector("#app"), { childList: true }));` });
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/proposal` });
  await waitFor(`document.querySelector('[data-testid="artwork-panel"]') && ${idle}`, "artwork panel", 300000);
  await waitFor(`document.querySelector("${img}")?.naturalWidth > 0`, "artwork image");
  await evaluate(`document.querySelector('[data-testid="artwork-panel"]').scrollIntoView({block: "start"})`);
  await evaluate(`document.querySelector("#art-viewport").focus()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "+", code: "Equal", windowsVirtualKeyCode: 187 });  // zoom in once
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "+", code: "Equal", windowsVirtualKeyCode: 187 });
  await waitFor(`document.querySelector('[data-testid="art-zoom-label"]')?.innerText !== "fit"`, "zoomed");
  results.zoom_before = await text('[data-testid="art-zoom-label"]');
  await clickSel('[data-testid="art-mode-line"]');
  await evaluate(`(() => { const i = document.querySelector("#art-width"); i.value = "2"; i.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  await clickSel('[data-testid="art-palette-color"][data-color="#774627"]');
  await waitFor(`document.querySelector('[data-testid="art-chosen"]')?.innerText.includes("#774627")`, "color chosen");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/projects/*/artwork/preview", requestStage: "Request" },
    { urlPattern: "*/api/projects/*/artwork/session/add", requestStage: "Request" }] });

  // 1. A line: its preview is held. The stroke is shown at once and marked as in progress.
  await drag(LINE_A);
  const previewA = await nextPaused("preview of line A");
  await waitFor(`document.querySelector('[data-testid="art-pending"]')`, "pending status");
  results.preview_held = await snap(LINE_A);
  await shot("pending_preview_held");
  // A second line and Done while it runs: nothing is sent.
  await drag(LINE_B);
  await enter();
  await sleep(800);
  results.requests_while_held = paused.length;
  await release(previewA);
  await waitFor(previewShown, "preview A shown");
  results.preview_shown = await snap(LINE_A);
  step("preview held, shown");

  // 2. Done: the add is held (in progress), then applied.
  await enter();
  const addA = await nextPaused("add of line A");
  await waitFor(`document.querySelector('[data-testid="art-pending"]')`, "applying status");
  results.add_held = await snap(LINE_A);
  await release(addA);
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("1 unsaved") && !document.querySelector('[data-testid="art-pending"]') && document.querySelector("${img}").src.includes("/artwork/session/current.png") && document.querySelector("${img}").complete`, "line A applied");
  results.added = await snap(LINE_A);
  await shot("pending_applied");
  step("add held, applied");

  // 3. Line B: preview passes, the add fails. The shape is kept and previewed again.
  await drag(LINE_B);
  await release(await nextPaused("preview of line B"));
  await waitFor(previewShown, "preview B shown");
  await enter();
  await fail(await nextPaused("add of line B"));
  await release(await nextPaused("re-preview of line B"));
  await waitFor(`${previewShown} && (document.querySelector('[data-testid="art-last"]')?.innerText || "").startsWith("Not changed")`, "add B failed, previewed again");
  results.add_failed = await snap(LINE_B);
  await shot("pending_add_failed");
  await enter();  // retry: applied
  await release(await nextPaused("retried add of line B"));
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("2 unsaved") && !document.querySelector('[data-testid="art-pending"]')`, "line B applied");
  results.retried = await snap(LINE_B);
  step("add failed, retried");

  // 4. Line C: the preview fails. No stroke is left on the canvas; the current artwork is shown.
  await drag(LINE_C);
  await fail(await nextPaused("preview of line C"));
  await waitFor(`!document.querySelector('[data-testid="art-pending"]') && (document.querySelector('[data-testid="art-last"]')?.innerText || "").startsWith("Not drawn")`, "preview C failed");
  results.preview_failed = await snap(LINE_C);
  await shot("pending_preview_failed");
  await clickSel('[data-testid="art-remove"]');

  // 5. Save: exactly lines A and B.
  await send("Fetch.disable");
  await clickSel('[data-testid="art-save"]');
  await waitFor(`(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "") === "All changes saved" && !document.querySelector('[data-testid="art-pending"]')`, "saved");
  results.saved_points = await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => s.artwork.edits.map((e) => e.op.points_px))`);
  results.saved_zoom = await text('[data-testid="art-zoom-label"]');
  results.console_errors = consoleErrors;
  results.ok = true;
} catch (error) {
  try { results.debug = await evaluate(`({ log: window.__log.slice(-40), coords: document.querySelector("#art-coords")?.innerText, last: document.querySelector('[data-testid="art-last"]')?.innerText, pending: document.querySelector('[data-testid="art-pending"]')?.innerText || null })`); } catch {}
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
