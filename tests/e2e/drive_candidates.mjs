// Drives candidate switching on the Plan screen in a headless Chromium browser (Edge or Chrome) through
// the DevTools protocol: Review this candidate (beam_colors) -> Compare candidates (from Review layers)
// -> Review this candidate (beam_area) -> Compare candidates -> Resume saved review (beam_colors).
// No npm packages: Node >= 22 (built-in WebSocket).  Same bootstrap as drive_widening.mjs.
//
//   node tests/e2e/drive_candidates.mjs <app url> <browser exe> <unused> <screenshot dir> <project>
//
// The project (a finished proposal, no review yet) is prepared by tests/test_candidate_switch.py.
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

const project = process.argv[6];
const row = (name) => `[...document.querySelectorAll('[data-testid="candidates-section"] tbody tr')].find((r) => r.dataset.candidate === ${JSON.stringify(name)})`;  // the planner's name (the label is "Candidate N")
const rowInfo = (name) => evaluate(`(() => { const r = ${row(name)}; return r ? { text: r.innerText, button: r.querySelector('[data-testid="candidate-review"]')?.innerText ?? null } : null; })()`);
async function apiState() {
  return evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => ({ candidate: s.record.candidate, saved: Object.keys(s.proposal.saved_reviews || {}), selected: s.proposal.selected, edits: s.draft ? s.draft.edits.length : null, layers: s.draft ? s.draft.sheets.map((x) => x.id) : null, jobs: (s.record.attempts || []).length }))`);
}
async function reviewCandidate(name, step) {
  await waitFor(`${idle} && (${row(name)})?.querySelector('[data-testid="candidate-review"]')`, `button for ${name}`);
  const before = await rowInfo(name);
  await evaluate(`(${row(name)}).querySelector('[data-testid="candidate-review"]').click()`);
  await waitFor(`${idle} && document.querySelector('[data-testid="review-candidate"]')?.dataset.candidate === ${JSON.stringify(name)} && document.querySelectorAll('[data-testid="layer-row"]').length > 1`, `review of ${name}`, 300000);
  await shot(`${step}_review_${name}`);
  results.steps.push({ step, clicked: before, review_bar: await text('[data-testid="review-candidate"]'), state: await apiState() });
}
async function backToComparison(step) {
  await clickSel('[data-testid="review-to-candidates"]');
  await waitFor(`document.querySelector('[data-testid="candidates-section"]')?.open`, "comparison open");
  await sleep(400);
  const visible = await evaluate(`(() => { const b = document.querySelector('[data-testid="candidates-section"]').getBoundingClientRect(); return b.top < innerHeight && b.bottom > 0; })()`);
  await shotEl(`${step}_comparison`, '[data-testid="candidates-section"]');
  results.steps.push({ step, comparison_visible: visible, tables: await count('[data-testid="candidates-section"] table'),
    rows: { beam_area: await rowInfo("beam_area"), beam_colors: await rowInfo("beam_colors") } });
}

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/proposal` });
  await waitFor(`${idle} && document.querySelector('[data-testid="candidates-section"]')`, "plan screen", 120000);
  await evaluate(`document.querySelector('[data-testid="candidates-section"]').open = true`);
  results.start = await apiState();
  await reviewCandidate("beam_colors", "1");
  await backToComparison("2");
  await reviewCandidate("beam_area", "3");
  await backToComparison("4");
  await reviewCandidate("beam_colors", "5");
  results.console_errors = consoleErrors;
  results.dialogs = dialogs;
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
