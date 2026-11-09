// Drives the Artwork editor of the real LayerForge app in a headless Chromium browser
// (Edge or Chrome) through the DevTools protocol: real clicks and pointer drags on the artwork.
// No npm packages: Node >= 22 (built-in WebSocket).  Same bootstrap as drive_app.mjs.
//
//   node tests/e2e/drive_artwork.mjs <app url> <browser exe> <image> <screenshot dir>
//
// The image is the sailboat fixture (512 px target at scale 1): portholes at rows 352, columns
// 210/250/290 share the sky color; the sun is at row 90, column 420.  Prints one JSON line.

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
const PORTHOLES = [[352, 210], [352, 250], [352, 290]];
const SUN = [90, 420];
const SUN_HEX = "#F9C832";
const history = () => count('[data-testid="art-history"] li');

try {
  // 1. Import without planning: intake only, then the artwork panel.
  await send("Page.navigate", { url });
  await waitFor(`document.querySelector("#dropzone")`, "import view");
  const doc = await send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#file-input" });
  await send("DOM.setFileInputFiles", { nodeId, files: [resolve(imagePath)] });
  await waitFor(`document.querySelector("#upload-preview")`, "upload preview");
  await evaluate(`(() => { const el = document.querySelector("#f-scale"); el.value = "1"; el.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  await clickSel('[data-testid="plan-later"]');
  results.create_label = await text('[data-action="create"]');
  await clickSel('[data-action="create"]');
  await waitFor(`${bodyHas("Artwork prepared")} && ${idle} && document.querySelector('[data-testid="artwork-panel"]')`, "artwork prepared", 120000);
  results.not_planned_banner = await text(".banner.info");
  await waitFor(`document.querySelector("#art-image")?.naturalWidth > 0`, "artwork image");
  step("artwork prepared, not planned");

  // 2. Click one porthole: only that connected region is selected and highlighted.
  await artClick(PORTHOLES[0]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "selection");
  await waitFor(`document.querySelector("#art-image").src.includes("selection.png")`, "highlight image");
  results.selection = await text('[data-testid="art-selection"]');
  await shot("a1_region_selected");
  await shotEl("art_selected", '[data-testid="artwork-panel"] .grid2');
  step("porthole selected", { selection: results.selection.split("\n")[0] });

  // 3. Select mode: choosing a color recolors the selection at once, as an unsaved change (no Apply).
  const unsavedIs = (n) => (n ? `(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("${n} unsaved change")`
    : `document.querySelector('[data-testid="art-unsaved"]')?.innerText === "All changes saved"`);
  const unsavedText = () => text('[data-testid="art-unsaved"]');
  const artState = () => evaluate(`fetch("/api/projects/" + location.hash.split("/")[1]).then((r) => r.json()).then((s) => s.artwork)`);
  results.no_apply_button = (await count('[data-testid="art-apply"]')) === 0;
  results.save_disabled_at_start = await evaluate(`document.querySelector('[data-testid="art-save"]').disabled`);
  await clickSel(`[data-testid="art-palette-color"][data-color="${SUN_HEX}"]`);
  await waitFor(unsavedIs(1), "first unsaved change");
  await waitFor(`document.querySelector("#art-image").src.includes("/artwork/session/current.png")`, "working image shown");
  results.first = {
    unsaved: await unsavedText(), last: await text('[data-testid="art-last"]'), history: await text('[data-testid="art-history"]'),
    plan_disabled: await evaluate(`document.querySelector('[data-testid="art-replan-button"]')?.disabled ?? null`),
    callout: await count('[data-testid="art-try-again-callout"]'), saved_edits: (await artState()).edits.length,
    summary: await text('[data-testid="artwork-panel"] summary'),
  };
  await shot("s1_unsaved_change");
  await shotEl("art_savebar", '[data-testid="art-savebar"]');
  await clickSel('[data-testid="art-session-undo"]');
  await waitFor(unsavedIs(0), "undone");
  results.first.redo_enabled = await evaluate(`!document.querySelector('[data-testid="art-session-redo"]').disabled`);
  await clickSel('[data-testid="art-session-redo"]');
  await waitFor(unsavedIs(1), "redone");
  step("instant recolor, undo, redo", { unsaved: results.first.unsaved });

  // 4. Eyedropper: the second porthole takes the sun's exact color, also at once (2 unsaved changes).
  await artClick(PORTHOLES[1]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "second porthole");
  await clickSel('[data-testid="art-eyedropper"]');
  await artClick(SUN);
  await waitFor(unsavedIs(2), "eyedropper recolor");
  results.history_unsaved = await text('[data-testid="art-history"]');

  // 5. Save Changes: both are saved together; then the planning action appears.
  await clickSel('[data-testid="art-save"]');
  await waitFor(`${unsavedIs(0)} && document.querySelectorAll('[data-testid="art-history"] li').length === 2`, "saved together");
  await waitFor(`document.querySelector('[data-testid="art-try-again"]')`, "planning action after save");
  results.saved_last = await text('[data-testid="art-last"]');
  results.history_text = await text('[data-testid="art-history"]');
  results.saved_batches = [...new Set((await artState()).edits.map((e) => e.batch))];
  results.try_again = await text('[data-testid="art-try-again-callout"]');
  results.try_again_in_view = await evaluate(`(() => { const b = document.querySelector('[data-testid="art-try-again"]').getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight; })()`);
  await shot("s2_saved");
  await shotEl("art_try_again", '[data-testid="art-try-again-callout"]');
  step("saved together", { history: results.history_text.split(String.fromCharCode(10)).length });

  // 6. Saved history: undo the last saved edit, verify. Then one unsaved change: leaving the project
  // asks first (dismissed: stays), a reload asks too and the change is recovered; Discard asks to confirm.
  await clickSel('[data-testid="art-undo"]');
  await waitFor(`document.querySelectorAll('[data-testid="art-history"] li').length === 1`, "undo saved");
  await clickSel('[data-testid="art-verify"]');
  await waitFor(`document.querySelector('[data-testid="art-verify-result"]')`, "verify");
  results.verify = await text('[data-testid="art-verify-result"]');
  await artClick(PORTHOLES[1]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "porthole for the recovery check");
  await clickSel('[data-testid="art-palette-color"][data-color="#774627"]');
  await waitFor(unsavedIs(1), "unsaved before leaving");
  dialogAccept = false;
  await evaluate(`location.hash = "#/"`);
  for (let i = 0; i < 50 && !dialogs.length; i++) await sleep(100);
  await sleep(400);
  results.leave = { dialog: dialogs[0] || null, hash: await evaluate("location.hash"), unsaved: await unsavedText(),
                    panel: await count('[data-testid="artwork-panel"]') };
  dialogAccept = true;
  await evaluate("window.__beforeReload = true");
  await send("Page.reload");
  await waitFor(`!window.__beforeReload && document.querySelector('[data-testid="artwork-panel"]') && ${idle} && ${unsavedIs(1)}`, "reloaded with the unsaved change");
  results.recovery = { unsaved: await unsavedText(), recovered: await text('[data-testid="art-recovered"]'),
                       dialogs: dialogs.map((d) => d.type) };
  await clickSel('[data-testid="art-discard"]');
  await waitFor(`document.querySelector('[data-testid="art-discard-confirm"]')`, "discard confirmation");
  results.discard_confirm = await text('[data-testid="art-savebar"]');
  await shotEl("art_discard", '[data-testid="art-savebar"]');
  await clickSel('[data-testid="art-discard-confirm"]');
  await waitFor(unsavedIs(0), "discarded");
  results.history_after_reload = await history();
  step("undo, verify, leave warning, recovery, discard", { verify: results.verify, recovery: results.recovery });

  // 7. Limit box: drag a box over the left sea, then select inside it.
  await clickSel('[data-testid="art-mode-box"]');
  await artDrag([400, 0], [511, 255]);
  await waitFor(`document.querySelector('[data-testid="art-clear-box"]')`, "box set");
  await artClick([480, 40]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "box selection");
  results.box_selection = await text('[data-testid="art-selection"]');
  await shot("a4_box_selection");
  await shotEl("art_box", '[data-testid="artwork-panel"] .grid2');
  await clickSel('[data-testid="art-clear-selection"]');
  await clickSel('[data-testid="art-clear-box"]');
  await shotEl("art_panel", '[data-testid="artwork-panel"]');

  // 7b. Zoom: plain mouse wheel toward the cursor (no modifier), the page does not scroll meanwhile;
  // wheel down zooms out; Fit resets; keyboard + / 0 on the focused viewport; hit-testing stays exact.
  const fitActive = () => evaluate(`document.querySelector('[data-testid="art-zoom-fit"]').classList.contains("active")`);
  results.zoom = { fit: await zoomLabel(), fit_active_at_start: await fitActive(), no_plus_minus_buttons: await count('[data-testid="art-zoom-in"], [data-testid="art-zoom-out"]') === 0 };
  const target = await artPoint(PORTHOLES[1]);
  const scrollBefore = await evaluate("scrollY");
  for (let i = 0; i < 6; i++) {
    await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: target.x, y: target.y, deltaX: 0, deltaY: -120 });
    await sleep(80);
  }
  results.zoom.after_wheel = await zoomLabel();
  results.zoom.page_scrolled_while_zooming = (await evaluate("scrollY")) !== scrollBefore;
  const kept = await artPoint(PORTHOLES[1], false);  // the pixel under the cursor stays under it
  results.zoom.anchor_drift_px = Math.hypot(kept.x - target.x, kept.y - target.y);
  await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: target.x, y: target.y, deltaX: 0, deltaY: 120 });
  await sleep(120);
  results.zoom.after_wheel_down = await zoomLabel();
  const keptDown = await artPoint(PORTHOLES[1], false);
  results.zoom.anchor_drift_down_px = Math.hypot(keptDown.x - target.x, keptDown.y - target.y);
  results.zoom.fit_active_when_zoomed = await fitActive();
  await clickSel('[data-testid="art-zoom-fit"]');
  results.zoom.after_fit = await zoomLabel();
  await evaluate(`document.querySelector("#art-viewport").focus()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "+", code: "Equal", text: "+", windowsVirtualKeyCode: 187 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "+", code: "Equal", windowsVirtualKeyCode: 187 });
  await sleep(100);
  results.zoom.after_key_plus = await zoomLabel();
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "0", code: "Digit0", text: "0", windowsVirtualKeyCode: 48 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "0", code: "Digit0", windowsVirtualKeyCode: 48 });
  await sleep(100);
  results.zoom.after_key_zero = await zoomLabel();
  // Outside the viewport the wheel scrolls the page as usual.
  await evaluate("window.scrollTo(0, 0)");
  await sleep(100);
  const header = await evaluate(`(() => { const b = document.querySelector(".project-head").getBoundingClientRect(); return {x: b.left + 40, y: b.top + 10}; })()`);
  await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: header.x, y: header.y, deltaX: 0, deltaY: 300 });
  await sleep(400);
  results.zoom.page_scrolls_outside = (await evaluate("scrollY")) > 0;
  const again = await artPoint(PORTHOLES[1]);
  for (let i = 0; i < 6; i++) {
    await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: again.x, y: again.y, deltaX: 0, deltaY: -120 });
    await sleep(60);
  }
  results.zoom.screen_px_per_art_px = await evaluate(`(() => { const i = document.querySelector("#art-image"); return i.getBoundingClientRect().width / i.naturalWidth; })()`);
  await artClick(PORTHOLES[1]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "zoomed selection");
  results.zoomed_selection = await text('[data-testid="art-selection"]');
  await clickSel('[data-testid="art-eyedropper"]');
  await artClick([352, 230]);  // hull between two portholes, a few pixels from each: recolors the porthole at once
  await waitFor(unsavedIs(1), "zoomed eyedropper");
  results.zoomed_eyedropper = await text('[data-testid="art-chosen"]');
  await shot("a6_zoomed");
  await shotEl("art_zoomed", '[data-testid="artwork-panel"] .grid2');
  await evaluate(`document.querySelector("#art-viewport").focus()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "z", code: "KeyZ", windowsVirtualKeyCode: 90, modifiers: MODIFIERS.ctrl });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "z", code: "KeyZ", windowsVirtualKeyCode: 90, modifiers: MODIFIERS.ctrl });
  await waitFor(unsavedIs(0), "Ctrl+Z undo");
  results.ctrl_z = await unsavedText();

  // 7c. Pan with Space + drag and with the middle button; clicks stay exact after panning.
  await evaluate(`document.querySelector("#art-viewport").scrollIntoView({block: "center"})`);
  await sleep(150);
  const vp = await viewportRect();
  const centre = { x: vp.x + vp.w / 2, y: vp.y + vp.h / 2 };
  await evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()");
  const t0 = await stageTransform();
  await key("keyDown", "Space", " ", 32);
  await screenDrag(centre, { x: centre.x + 60, y: centre.y + 40 });
  await key("keyUp", "Space", " ", 32);
  const t1 = await stageTransform();
  await screenDrag(centre, { x: centre.x - 30, y: centre.y - 20 }, "middle");
  const t2 = await stageTransform();
  results.pan = { before: t0, space_drag: t1, middle_drag: t2, history: await history() };
  await artClick(PORTHOLES[1]);  // after panning: still the same porthole (a click in Select mode only selects)
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "panned selection");
  results.panned_selection = await text('[data-testid="art-selection"]');
  await artClick(PORTHOLES[1], MODIFIERS.shift);  // Shift+Click a selected region: removed
  await waitFor(`!document.querySelector('[data-testid="art-selection"]')`, "deselected");
  results.pan.unsaved_after = await unsavedText();
  step("zoom and pan", { zoom: results.zoom, pan: results.pan });

  // 7d. Multi-region selection (Shift+Click, mixed colors) recolored at once as one change.
  await clickSel('[data-testid="art-zoom-fit"]');
  await artClick(PORTHOLES[1]);
  await waitFor(`document.querySelector('[data-testid="art-selection-count"]')`, "first region");
  await artClick(PORTHOLES[2], MODIFIERS.shift);
  await waitFor(`document.querySelector('[data-testid="art-selection-count"]')?.innerText.startsWith("2 regions")`, "second region");
  await artClick(SUN, MODIFIERS.shift);
  await waitFor(`document.querySelector('[data-testid="art-selection-count"]')?.innerText.startsWith("3 regions")`, "third region");
  results.multi = { three: await text('[data-testid="art-selection"]') };
  await artClick(PORTHOLES[2], MODIFIERS.shift);
  await waitFor(`document.querySelector('[data-testid="art-selection-count"]')?.innerText.startsWith("2 regions")`, "removed one");
  results.multi.after_remove = await selectionCount();
  await clickSel('[data-testid="art-palette-color"][data-color="#774627"]');
  await waitFor(unsavedIs(1), "batch recolor");
  results.multi.history = await text('[data-testid="art-history"]');
  results.multi.callout_while_unsaved = await count('[data-testid="art-try-again-callout"]');
  await shot("a7_multi");
  await shotEl("art_multi", '[data-testid="artwork-panel"] .grid2');
  await clickSel('[data-testid="art-session-undo"]');  // the whole batch is one undo step
  await waitFor(unsavedIs(0), "batch undone");
  results.multi.after_undo = await history();
  results.multi.try_again = await evaluate(`Boolean(document.querySelector('[data-testid="art-try-again"]'))`);
  step("multi-region batch", { after_remove: results.multi.after_remove });

  // 7e. Several edits in a row, each shown at once and building on the previous ones, saved together:
  // Fill a porthole; a Line (end point dragged, angle typed, Enter = Done); a Curve across the sail bent
  // by a control point (finished by switching tool); Fill of the sail part the unsaved curve cut off;
  // a Brush stroke inside the sun; a curved Close gap (Done). Undo / redo single changes, then Save.
  const paint = {};
  const shapePoints = () => text('[data-testid="art-shape-points"]');
  const strokeInfo = () => waitFor(`document.querySelector('[data-testid="art-stroke-info"]')`, "shape preview");
  const setArtField = (id, value) => evaluate(`(() => { const i = document.querySelector("#${id}"); i.value = "${value}"; i.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  const lastOp = async () => { const a = await artState(); return a.unsaved.ops[a.unsaved.ops.length - 1]; };
  await clickSel('[data-testid="art-mode-fill"]');
  paint.fill_empty = await text('[data-testid="art-empty"]');
  await clickSel('[data-testid="art-palette-color"][data-color="#774627"]');  // a color before any selection
  await artClick(PORTHOLES[0]);
  await waitFor(unsavedIs(1), "fill");
  paint.fill_last = await text('[data-testid="art-last"]');
  await clickSel('[data-testid="art-mode-line"]');
  await evaluate(`(() => { const i = document.querySelector("#art-width"); i.value = "1.0"; i.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  await waitFor(`document.querySelector('[data-testid="art-width-warn"]')`, "thin-line warning");
  paint.width_warn = await text('[data-testid="art-width-warn"]');
  await artDrag([60, 40], [60, 200]);
  await strokeInfo();
  paint.line_preview = await text('[data-testid="art-stroke-info"]');
  paint.line_points = await shapePoints();
  paint.unsaved_while_pending = await unsavedText();  // a pending shape is not an edit yet
  await artDrag([60, 200], [80, 200]);  // drag the end point: previewed again
  await waitFor(`document.querySelector('[data-testid="art-shape-points"]')?.innerText.includes("(80, 200)")`, "end point moved");
  paint.line_moved = await shapePoints();
  await setArtField("art-angle", "0");  // horizontal again, same start and length
  await waitFor(`(document.querySelector('[data-testid="art-shape-points"]')?.innerText || "").split("→")[1]?.trim().startsWith("(60,")`, "angle applied");
  paint.line_angle = await shapePoints();
  await waitFor(`document.querySelector("#art-image").src.includes("/artwork/preview/after.png") && document.querySelector("#art-image").complete`, "line preview shown");
  await shot("a8_line_pending");
  await evaluate(`document.querySelector("#art-viewport").focus()`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await waitFor(`${unsavedIs(2)} && !document.querySelector('[data-testid="art-shape"]')`, "line done with Enter");
  await clickSel('[data-testid="art-mode-curve"]');
  await artDrag([250, 240], [250, 345]);  // across the mast and the sail
  await strokeInfo();
  paint.curve_handles = await count('[data-testid="art-shape"]');
  const curveImage = await evaluate(`document.querySelector("#art-image").src`);
  await artDrag([229, 275], [285, 275]);  // drag control point 1 down: the curve bends, previewed again
  await waitFor(`document.querySelector("#art-image")?.src !== ${JSON.stringify(curveImage)} && document.querySelector("#art-image").src.includes("/artwork/preview/after.png") && document.querySelector("#art-image").complete && document.querySelector('[data-testid="art-stroke-info"]')`, "curve re-previewed");
  paint.curve_preview = await text('[data-testid="art-stroke-info"]');
  await shot("a10_curve_pending");
  await clickSel('[data-testid="art-mode-fill"]');  // a tool change finishes the pending curve
  await waitFor(unsavedIs(3), "curve done by tool change");
  await clickSel('[data-testid="art-palette-color"][data-color="#1C478D"]');
  await artClick([180, 266]);  // the upper sail, cut off by the unsaved curve
  await waitFor(unsavedIs(4), "fill on the unsaved curve's region");
  paint.fill_split = (await lastOp()).change;
  await clickSel('[data-testid="art-mode-brush"]');
  paint.brush_empty = await text('[data-testid="art-empty"]');
  await artClick(SUN);  // in Brush mode a click selects where to paint, it does not recolor
  await waitFor(`document.querySelector('[data-testid="art-brush-note"]')`, "brush selection");
  paint.brush_click_unsaved = await unsavedText();
  await artDrag([SUN[0], SUN[1] - 80], [SUN[0], SUN[1] + 80]);
  await waitFor(unsavedIs(5), "brush stroke");
  await waitFor(`document.querySelector('[data-testid="art-brush-note"]')`, "selection kept for the next stroke");
  paint.brush_note = await text('[data-testid="art-brush-note"]');
  paint.brush_op = (await lastOp()).op;
  await shot("a9_brush");
  await clickSel('[data-testid="art-mode-gap"]');
  await clickSel('[data-testid="art-palette-color"][data-color="#AAD7F4"]');
  await artClick([PORTHOLES[1][0], PORTHOLES[1][1] + 3]);
  paint.gap_first = await text("#art-coords");
  await artClick(PORTHOLES[2]);
  await strokeInfo();
  paint.gap_info = await text('[data-testid="art-stroke-info"]');
  await clickSel('[data-testid="art-gap-cubic"]');  // a curved connection between the same snapped ends
  await waitFor(`document.querySelector('[data-testid="art-stroke-info"]')?.innerText.includes("curved")`, "curved gap");
  paint.gap_curved = await text('[data-testid="art-stroke-info"]');
  await clickSel('[data-testid="art-done"]');
  await waitFor(unsavedIs(6), "gap done");
  paint.unsaved_history = await text('[data-testid="art-history"]');
  await shot("s3_six_unsaved");
  await shotEl("art_unsaved_history", '[data-testid="artwork-panel"]');
  await clickSel('[data-testid="art-session-undo"]');
  await waitFor(unsavedIs(5), "undo 1");
  await clickSel('[data-testid="art-session-undo"]');
  await waitFor(unsavedIs(4), "undo 2");
  paint.after_undo = { unsaved: await unsavedText(), redo: (await artState()).unsaved.redo };
  await clickSel('[data-testid="art-session-redo"]');
  await waitFor(unsavedIs(5), "redo 1");
  await clickSel('[data-testid="art-session-redo"]');
  await waitFor(unsavedIs(6), "redo 2");
  paint.saved_before = (await artState()).edits.length;
  await clickSel('[data-testid="art-save"]');
  await waitFor(`${unsavedIs(0)} && document.querySelectorAll('[data-testid="art-history"] li').length === 7`, "six saved together");
  paint.history = await text('[data-testid="art-history"]');
  paint.batches = (await artState()).edits.map((e) => e.batch);
  await shot("s4_saved_batch");
  await clickSel('[data-testid="art-history"] [data-action="art-undo"][data-keep="1"]');
  await waitFor(`document.querySelectorAll('[data-testid="art-history"] li').length === 1`, "paint reverted");
  paint.after_revert = await history();
  results.paint = paint;
  step("paint tools", { history: paint.history.split(String.fromCharCode(10)).length });

  // 8. Explicit planning from the edited artwork; palette and measured sheet count shown.
  results.replan_label = await text('[data-testid="art-replan-button"]');
  await clickSel('[data-testid="art-try-again"]');
  await waitFor(`document.querySelector('[data-testid="job-banner"]')`, "replan job", 15000);
  await waitFor(`${bodyHas("Ready for review")} && ${idle} && document.querySelector('[data-testid="artwork-check"]')`, "replanned", 300000);
  results.artwork_check = await text('[data-testid="artwork-check"]');
  results.check_after_row = await text('[data-testid="artwork-check-after"]');
  await shot("a5_replanned");
  await shotEl("art_check", '[data-testid="artwork-check"]');
  step("planned from the edited artwork", { after: results.check_after_row });

  // 9. Review Layers canvas (inspection): wheel zoom toward the cursor, an exact mm point marked by a click
  // at high zoom, panning (middle drag) that never moves the mark.
  await clickSel('[data-action="start-review"]');
  await waitFor(`document.querySelector("#edit-viewport") && document.querySelector("#edit-image")?.naturalWidth > 0 && ${idle}`, "review", 300000);
  await clickSel('[data-testid="view-switch"] [data-view="sheet"]');
  await waitFor(`document.querySelector("#edit-image")?.src.includes("/sheets/") && document.querySelector("#edit-image").complete && document.querySelector("#edit-image").naturalWidth > 0`, "sheet view loaded");
  await sleep(300);
  const editPoint = async ([x, y], scroll = true) => {
    if (scroll) { await evaluate(`document.querySelector("#edit-viewport").scrollIntoView({block: "center"})`); await sleep(150); }
    const r = await evaluate(`(() => { const b = document.querySelector("#edit-image").getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height}; })()`);
    return { x: r.x + (x / 225) * r.w, y: r.y + (1 - y / 225) * r.h };
  };
  const P = [60.2, 40.3];
  const rz = { fit: await text('[data-testid="edit-zoom-label"]') };
  const p0 = await editPoint(P);
  // Mouse events carry whole pixels: zoom at an integer point and follow the image point under it.
  const cursor = { x: Math.round(p0.x), y: Math.round(p0.y) };
  const imageRect = () => evaluate(`(() => { const b = document.querySelector("#edit-image").getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height}; })()`);
  const rb = await imageRect();
  const frac = [(cursor.x - rb.x) / rb.w, (cursor.y - rb.y) / rb.h];
  const sy = await evaluate("scrollY");
  for (let i = 0; i < 8; i++) {
    await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: cursor.x, y: cursor.y, deltaX: 0, deltaY: -120 });
    await sleep(60);
  }
  rz.after_wheel = await text('[data-testid="edit-zoom-label"]');
  rz.page_scrolled = (await evaluate("scrollY")) !== sy;
  const ra = await imageRect();
  rz.anchor_drift_px = Math.hypot(ra.x + frac[0] * ra.w - cursor.x, ra.y + frac[1] * ra.h - cursor.y);
  const p1 = await editPoint(P, false);
  rz.screen_px_per_image_px = await evaluate(`(() => { const i = document.querySelector("#edit-image"); return i.getBoundingClientRect().width / i.naturalWidth; })()`);
  await mouse("mouseMoved", p1, 0);
  await mouse("mousePressed", p1);
  await mouse("mouseReleased", p1, 0);
  const marker = () => evaluate(`JSON.parse(document.querySelector("#edit-viewport").dataset.marker || "null")`);
  await waitFor(`JSON.parse(document.querySelector("#edit-viewport").dataset.marker || "null")`, "point marked at high zoom");
  const pending = JSON.stringify(await marker());
  rz.clicked_mm = await marker();
  rz.target_mm = P;
  const e0 = await evaluate(`document.querySelector("#edit-stage").style.transform`);
  const c = await viewportRect();
  await screenDrag({ x: c.x + c.w / 2, y: c.y + c.h / 2 }, { x: c.x + c.w / 2 + 80, y: c.y + c.h / 2 + 50 }, "middle");
  rz.panned = (await evaluate(`document.querySelector("#edit-stage").style.transform`)) !== e0;
  rz.pending_unchanged_by_pan = JSON.stringify(await marker()) === pending;
  rz.pending_edits = await count('[data-testid="pending-edit"]');
  await shot("a8_review_zoomed");
  await clickSel('[data-testid="edit-zoom-fit"]');
  rz.after_fit = await text('[data-testid="edit-zoom-label"]');
  results.review_zoom = rz;
  step("review canvas zoom", rz);

  // 10. Review Layers edits no sheet geometry (no Line, Curve, Fill, Cut, ... tool); the Artwork editor is on it.
  results.review_tools = { tools: await count('#tab-body [data-tool], #tab-body .toolbar'), artwork_panel: await count('#tab-body [data-testid="artwork-panel"]') };
  await clickSel('[data-step="artwork"]');
  await sleep(300);
  results.review_tools.artwork_tab = await evaluate("location.hash");
  step("review is read-only", results.review_tools);

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
