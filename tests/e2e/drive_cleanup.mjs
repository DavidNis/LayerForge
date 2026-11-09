// Drives the Color cleanup section of the Plan screen of the real LayerForge app in a headless Chromium
// browser (Edge or Chrome) through the DevTools protocol: the collapsed summary, the before / after
// toggle, and turning the cleanup off and on again with a re-run.  No npm packages: Node >= 22.  Same
// bootstrap as drive_review.mjs.
//
//   node tests/e2e/drive_cleanup.mjs <app url> <browser exe> <unused> <screenshot dir> <project>
//
// The project (a grainy drawing, intake only) is prepared through the API by tests/test_speckle_cleanup.py.
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
function step(name, detail = {}) {
  results.steps.push({ name, ...detail });
  console.error(`[e2e] ${name} ${JSON.stringify(detail)}`);
}
const [, , , , project] = process.argv.slice(2);
const P = '[data-testid="cleanup-panel"]';
const panel = () => evaluate(`(() => { const p = document.querySelector(${JSON.stringify(P)}); if (!p) return null;
  const img = p.querySelector('[data-testid="cleanup-image"]');
  return { open: p.open, summary: p.querySelector("summary").innerText.replace(/\\s+/g, " ").trim(),
    words: p.innerText.trim().split(/\\s+/).length, buttons: p.querySelectorAll("button").length,
    src: img?.getAttribute("src") || "", views: [...p.querySelectorAll('[data-action="cleanup-view"]')].map((b) => b.innerText),
    active: p.querySelector('[data-action="cleanup-view"].active')?.dataset.view || "",
    checked: p.querySelector('#cleanup-on')?.checked ?? null, rerun: Boolean(p.querySelector('[data-action="apply-cleanup"]')) }; })()`);
async function rerun(what) {
  await clickSel('[data-action="apply-cleanup"]');
  await waitFor(`document.querySelector('[data-testid="job-banner"]')`, `${what} started`, 30000).catch(() => {});
  await waitFor(`${idle} && document.querySelector(${JSON.stringify(P)})`, `${what} done`, 300000);
}
async function toggle() {
  await evaluate(`(() => { const c = document.querySelector('#cleanup-on'); c.click(); })()`);
  await sleep(150);
}

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/proposal` });
  await waitFor(`document.querySelector(${JSON.stringify(P)}) && ${idle}`, "plan page", 120000);
  results.collapsed = await panel();
  step("collapsed", results.collapsed);

  await clickSel(`${P} > summary`);
  await sleep(150);
  results.after = await panel();
  await clickSel(`${P} [data-action="cleanup-view"][data-view="before"]`);
  await sleep(150);
  results.before = await panel();
  await shotEl("cleanup_before", P);
  await clickSel(`${P} [data-action="cleanup-view"][data-view="after"]`);
  await sleep(150);
  results.after_again = await panel();
  await shotEl("cleanup_after", P);
  step("before / after", { after: results.after.src, before: results.before.src });

  await toggle();
  results.toggled_off = await panel();
  await rerun("cleanup off");
  results.off = await panel();
  await shotEl("cleanup_off", P);
  step("off", results.off);

  if (!results.off.open) await clickSel(`${P} > summary`);
  await toggle();
  await rerun("cleanup on");
  results.on = await panel();
  step("on again", results.on);
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
