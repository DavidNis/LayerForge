// Drives "Fit artwork to canvas" in the Artwork editor of the real LayerForge app in a headless
// Chromium browser (Edge or Chrome) through the DevTools protocol.  No npm packages: Node >= 22.
// Same bootstrap as drive_artwork.mjs.
//
//   node tests/e2e/drive_fit.mjs <app url> <browser exe> <project> <screenshot dir>
//
// The project exists already (imported by the caller, intake only).  Mouse only: open the card
// (automatic fit), drag the subject to move it, drag the bottom-left handle to enlarge and to shrink it,
// drag far past the canvas edges (clamped), Fit automatically, Apply (one unsaved change), Save Changes.
// Prints one JSON line.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const [url, browserExe, project, shotDir] = process.argv.slice(2);
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

const fitCard = '[data-testid="art-fit"]';
const unsavedIs = (n) => `(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith(${JSON.stringify(n === 0 ? "All changes saved" : `${n} unsaved change`)})`;
const fitState = () => evaluate(`(() => { const c = document.querySelector("#fit-canvas"); return c ? {manual: JSON.parse(c.dataset.manual), op: JSON.parse(c.dataset.op), ap: JSON.parse(c.dataset.aperture), loading: c.dataset.loading === "1", stale: c.dataset.stale === "1", clipping: JSON.parse(c.dataset.clipping || "null")} : null; })()`);
// What the card says right now: its warnings, the "checking" line, Apply, the red overlay, the source-crop note.
const fitUi = () => evaluate(`(() => ({
  warnings: [...document.querySelectorAll('[data-testid="art-fit-warning"]')].filter((n) => n.checkVisibility({ visibilityProperty: true })).map((n) => n.innerText.trim()),
  checking: Boolean(document.querySelector('[data-testid="art-fit-checking"]')?.checkVisibility({ visibilityProperty: true })),
  apply_disabled: Boolean(document.querySelector('[data-testid="art-fit-apply"]')?.disabled),
  overlay: Boolean(document.querySelector('[data-testid="art-fit-clipped-overlay"]')?.checkVisibility({ visibilityProperty: true })),
  source_cropped: document.querySelector('[data-testid="art-fit-source-cropped"]')?.innerText || null,
}))()`);
// Screen point of a canvas (artwork) pixel of the live after canvas.
async function canvasPoint([row, col]) {
  const r = await evaluate(`(() => { const c = document.querySelector("#fit-canvas"); const b = c.getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height, cw: c.width, ch: c.height}; })()`);
  return { x: r.x + (col / r.cw) * r.w, y: r.y + (row / r.ch) * r.h };
}
async function canvasDrag(from, to, steps = 8) {
  await evaluate(`document.querySelector("#fit-canvas").scrollIntoView({block: "center"})`);
  await sleep(150);
  // Whole CSS pixels (the browser delivers mouse events at whole pixels); the canvas-pixel displacement
  // actually requested is returned with the live state.
  const snap = (q) => ({ x: Math.round(q.x), y: Math.round(q.y) });
  const a = snap(await canvasPoint(from));
  const b = snap(await canvasPoint(to));
  const box = await evaluate(`(() => { const c = document.querySelector("#fit-canvas"); const r = c.getBoundingClientRect(); return {w: r.width, h: r.height, cw: c.width, ch: c.height}; })()`);
  await mouse("mouseMoved", a, 0);
  await mouse("mousePressed", a);
  for (let i = 1; i <= steps; i++) await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / steps, y: a.y + ((b.y - a.y) * i) / steps });
  const live = await fitState();  // before release: the live (client) framing
  live.ui = await fitUi();
  live.requested = { dy: ((b.y - a.y) * box.ch) / box.h, dx: ((b.x - a.x) * box.cw) / box.w };         // ... and what the card says while the drag is still going on
  await mouse("mouseReleased", b, 0);
  return live;
}
// The server has checked the framing the user dragged (its op equals the client's framing).
const synced = `(() => { const c = document.querySelector("#fit-canvas"); if (!c || !c.dataset.manual) return false; const m = JSON.parse(c.dataset.manual), o = JSON.parse(c.dataset.op); return c.dataset.loading === "0" && c.dataset.stale === "0" && Boolean(m && o && o.scale === m.scale && o.offset_px[0] === m.offset_px[0] && o.offset_px[1] === m.offset_px[1]); })()`;
const rect = (m) => {
  const [r0, c0, r1, c1] = m.source_box_px;
  const h = Math.max(1, Math.floor((r1 - r0) * m.scale + 1e-9)), w = Math.max(1, Math.floor((c1 - c0) * m.scale + 1e-9));
  return { top: m.offset_px[0], left: m.offset_px[1], h, w, bottom: m.offset_px[0] + h, right: m.offset_px[1] + w };
};

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}` });
  await waitFor(`document.querySelector('[data-testid="art-fit-open"]') && ${idle}`, "fit button", 120000);
  await waitFor(`document.querySelector("#art-image")?.naturalWidth > 0`, "artwork image");
  // Headless Chromium reveals the page scrollbar at the first mouse press, which narrows the layout in the
  // middle of that drag; keep scrollbars hidden throughout so drags measure exactly.
  await send("Emulation.setScrollbarsHidden", { hidden: true });
  await shotEl("fit_closed", fitCard);

  // 1. Open: the automatic fit, only the preview and two buttons.
  await clickSel('[data-testid="art-fit-open"]');
  await waitFor(`document.querySelector("#fit-canvas") && ${synced}`, "automatic fit");
  await sleep(300);
  const auto = await fitState();
  results.controls = {
    buttons: await evaluate(`[...document.querySelectorAll('[data-testid="art-fit"] button')].map((b) => b.innerText.trim()).filter(Boolean)`),
    inputs: await count('[data-testid="art-fit"] input, [data-testid="art-fit"] select'),
    status: await text('[data-testid="art-fit-status"]'),
  };
  results.auto = { op: auto.op, ap: auto.ap, rect: rect(auto.manual), clipping: auto.clipping, ui: await fitUi() };
  await shotEl("fit_auto", fitCard);
  step("automatic fit", results.controls);

  // 2. Drag the subject: moved by the drag, same size.
  const r1 = rect(auto.manual);
  const centre = [r1.top + r1.h / 2, r1.left + r1.w / 2];
  const moveLive = await canvasDrag(centre, [centre[0] - 60, centre[1] + 40]);
  await waitFor(synced, "move checked");
  const moved = await fitState();
  results.move = { requested: moveLive.requested, before: r1, live: rect(moveLive.manual), after: rect(moved.manual), scale: moved.manual.scale, server_op: moved.op,
                   during: moveLive.ui, during_stale: moveLive.stale, clipping: moved.clipping, ui: await fitUi() };
  step("move", { after: results.move.after });

  // 3. Bottom-left handle outward (left and down): larger, top-right corner kept, proportions kept.
  const r2 = rect(moved.manual);
  const growLive = await canvasDrag([r2.bottom, r2.left], [r2.bottom + 40, r2.left - 60]);
  await waitFor(synced, "grow checked");
  const grown = await fitState();
  results.grow = { before: r2, live: rect(growLive.manual), after: rect(grown.manual), scale_before: moved.manual.scale, scale: grown.manual.scale };
  await shotEl("fit_resized", fitCard);
  step("grow", { after: results.grow.after, scale: results.grow.scale });

  // 4. Handle inward: smaller.
  const r3 = rect(grown.manual);
  await canvasDrag([r3.bottom, r3.left], [r3.bottom - 80, r3.left + 120]);
  await waitFor(synced, "shrink checked");
  const shrunk = await fitState();
  results.shrink = { before: r3, after: rect(shrunk.manual), scale_before: grown.manual.scale, scale: shrunk.manual.scale };
  step("shrink", { scale: results.shrink.scale });

  // 4b. Shrink below 100 %: only the reduction warning (no clipping, the subject is inside); then enlarge back
  // above 100 %: every warning goes away (no stale warning of the earlier framing stays on screen).
  const r3b = rect(shrunk.manual);
  const target = Math.round((r3b.w * 0.55) / shrunk.manual.scale);  // about 55 % of the source width
  await canvasDrag([r3b.bottom, r3b.left], [r3b.top + Math.round(target * r3b.h / r3b.w), r3b.right - target]);
  await waitFor(synced, "small checked");
  const small = await fitState();
  results.small = { scale: small.manual.scale, clipping: small.clipping, ui: await fitUi(), rect: rect(small.manual) };
  await shotEl("fit_below_100", fitCard);
  const r3c = rect(small.manual);
  const [b0, c0b, b1, c1b] = small.manual.source_box_px;  // handle to where 110 % of the source size puts it
  const growBack = await canvasDrag([r3c.bottom, r3c.left], [r3c.top + Math.ceil((b1 - b0) * 1.1), r3c.right - Math.ceil((c1b - c0b) * 1.1)]);
  await waitFor(synced, "large again checked");
  const large = await fitState();
  await shotEl("fit_above_100", fitCard);
  results.large_again = { scale: large.manual.scale, clipping: large.clipping, ui: await fitUi(), during: growBack.ui, during_stale: growBack.stale };
  step("below and above 100 %", { small: results.small.scale, large: results.large_again.scale });

  // 5. Far past the canvas: moving and resizing stop at the edges.
  const r4 = rect(shrunk.manual);
  await canvasDrag([r4.top + r4.h / 2, r4.left + r4.w / 2], [-400, auto.ap[1] + 400]);
  await waitFor(synced, "move clamped");
  const pushed = await fitState();
  const r5 = rect(pushed.manual);
  await canvasDrag([r5.bottom, r5.left], [auto.ap[0] + 600, -600]);
  await waitFor(synced, "resize clamped");
  const huge = await fitState();
  results.bounds = { pushed: rect(pushed.manual), huge: rect(huge.manual), ap: auto.ap, pushed_clipping: pushed.clipping,
                     clipping: huge.clipping, ui: await fitUi(), warnings: await count('[data-testid="art-fit-warning"]') };
  step("bounds", results.bounds);

  // 6. Fit automatically restores the detected fit; a small move; Apply adds one unsaved edit; Save.
  await clickSel('[data-testid="art-fit-auto"]');
  await waitFor(`${synced} && JSON.parse(document.querySelector("#fit-canvas").dataset.op).mode === "auto"`, "automatic again");
  const again = await fitState();
  results.auto_again = again.op;
  results.auto_again_ui = await fitUi();
  await canvasDrag(centre, [centre[0] + 10, centre[1] - 10]);
  await waitFor(synced, "final move checked");
  results.applied_op = (await fitState()).op;
  await clickSel('[data-testid="art-fit-apply"]');
  await waitFor(unsavedIs(1), "fit added to the session");
  await waitFor(`document.querySelector("#art-image").src.includes("/artwork/session/current.png") && document.querySelector("#art-image").complete`, "fitted artwork shown");
  results.after_apply = { card_closed: Boolean(await count('[data-testid="art-fit-open"]')), history: await text('[data-testid="art-history"]') };
  await shot("f02_fit_applied");
  await clickSel('[data-testid="art-save"]');
  await waitFor(`${unsavedIs(0)} && document.querySelector('[data-testid="art-try-again"]')`, "saved");
  results.saved = { history: await text('[data-testid="art-history"]'), replan_button: Boolean(await count('[data-testid="art-try-again"]')),
                    job_running: !(await evaluate(idle)) };
  await shot("f03_fit_saved");
  step("applied and saved", results.saved);

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
