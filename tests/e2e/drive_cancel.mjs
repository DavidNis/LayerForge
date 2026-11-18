// Stop a planning job from the page (D-V3-055) in a headless Chromium browser (Chrome or Edge): the Stop button
// in the progress bar, "Stopping…", the notice, a usable page, and planning again.  No npm packages: Node >= 22.
//
//   node tests/e2e/drive_cancel.mjs <app url> <browser exe> <project> <screenshot dir>
//
// The project (planned, no review) is prepared by tests/test_job_cancel.py.  Prints one JSON line.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, browserExe, project, shotDir] = process.argv.slice(2);
const results = { console_errors: [] };
mkdirSync(shotDir, { recursive: true });
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const profile = mkdtempSync(join(tmpdir(), "lf-e2e-"));
const browser = spawn(browserExe, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--disable-sync", "--no-default-browser-check", "--disable-extensions", "--window-size=1280,900", "about:blank"],
  { stdio: ["ignore", "ignore", "pipe"] });
const wsUrl = await new Promise((ok, fail) => {
  let buf = "";
  const timer = setTimeout(() => fail(new Error("browser did not start")), 30000);
  browser.stderr.on("data", (d) => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) { clearTimeout(timer); ok(m[1]); } });
});
const ws = new WebSocket(wsUrl);
await new Promise((ok) => ws.addEventListener("open", ok, { once: true }));
let nextId = 1;
const waiting = new Map();
let session;
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) { const { ok, fail } = waiting.get(msg.id); waiting.delete(msg.id); msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result); }
  else if (msg.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", { accept: true }).catch(() => {});
  else if (msg.method === "Runtime.exceptionThrown") results.console_errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
});
const send = (method, params = {}, sessionId = session) => new Promise((ok, fail) => {
  const id = nextId++; waiting.set(id, { ok, fail });
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const { targetId } = await send("Target.createTarget", { url: "about:blank" }, null);
session = (await send("Target.attachToTarget", { targetId, flatten: true }, null)).sessionId;
await send("Page.enable");
await send("Runtime.enable");
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function waitFor(expression, what, ms = 120000) {
  const start = Date.now();
  while (Date.now() - start < ms) { try { if (await evaluate(expression)) return; } catch { /* rendering */ } await sleep(100); }
  throw new Error(`timed out waiting for: ${what}`);
}
async function shot(name) {
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, "base64"));
}
const api = () => evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => ({ job: s.job?.status, seconds: s.proposal?.seconds, status: s.proposal?.status }))`);

try {
  await send("Page.navigate", { url: `${url}#/${encodeURIComponent(project)}/proposal` });
  await waitFor(`!!document.querySelector('[data-testid="plan-next"]')`, "plan screen");
  results.before = await api();
  // Re-run the proposal (as "apply detail choices" does), then stop it from the progress bar.
  await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}/propose", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(() => location.reload())`);
  await waitFor(`!!document.querySelector('[data-testid="job-cancel"]') && !document.querySelector('[data-testid="job-cancel"]').disabled`, "Stop button", 60000);
  await waitFor(`(document.querySelector('[data-testid="job-banner"]')?.innerText || "").match(/intake done|candidate/i)`, "planning under way", 120000);
  results.banner = await evaluate(`document.querySelector('[data-testid="job-banner"]').innerText`);
  await shot("c1_planning_with_stop");
  await evaluate(`document.querySelector('[data-testid="job-cancel"]').click(); true`);
  await sleep(150);
  results.stopping = await evaluate(`document.querySelector('[data-testid="job-cancel"]')?.innerText || ""`);
  await shot("c2_stopping");
  await waitFor(`!document.querySelector('[data-testid="job-banner"]') && !!document.querySelector('[data-testid="job-notice"]')`, "stopped", 120000);
  results.notice = await evaluate(`document.querySelector('[data-testid="job-notice"]').innerText`);
  results.after = await api();
  results.next_enabled = await evaluate(`!document.querySelector('[data-testid="plan-next-button"]')?.disabled`);
  await shot("c3_stopped");
  // Planning again right away works.
  await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}/propose", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(() => location.reload())`);
  await waitFor(`!!document.querySelector('[data-testid="job-banner"]')`, "second run", 60000);
  await waitFor(`!document.querySelector('[data-testid="job-banner"]') && !!document.querySelector('[data-testid="plan-next"]')`, "second run done", 300000);
  results.again = await api();
  // "Review this candidate" (D-V3-056): Stop while the review is being created; the Plan screen stays.
  await evaluate(`document.querySelector('[data-testid="plan-next-button"]').click(); true`);
  await waitFor(`!!document.querySelector('[data-testid="job-cancel"]') && !document.querySelector('[data-testid="job-cancel"]').disabled`, "review Stop button", 60000);
  results.review_banner = await evaluate(`document.querySelector('[data-testid="job-banner"]').innerText`);
  await shot("c4_review_with_stop");
  await evaluate(`document.querySelector('[data-testid="job-cancel"]').click(); true`);
  await waitFor(`!document.querySelector('[data-testid="job-banner"]') && !!document.querySelector('[data-testid="job-notice"]')`, "review stopped", 120000);
  results.review_notice = await evaluate(`document.querySelector('[data-testid="job-notice"]').innerText`);
  results.review_after = await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => ({ draft: !!s.draft, stage: s.stage }))`);
  results.review_plan_screen = await evaluate(`!!document.querySelector('[data-testid="plan-next-button"]') && !document.querySelector('[data-testid="plan-next-button"]').disabled`);
  await shot("c5_review_stopped");
  await evaluate(`document.querySelector('[data-testid="plan-next-button"]').click(); true`);
  await waitFor(`!!document.querySelector('[data-testid="job-banner"]')`, "review again", 60000);
  await waitFor(`!document.querySelector('[data-testid="job-banner"]')`, "review again done", 300000);
  results.review_again = await evaluate(`fetch("/api/projects/${encodeURIComponent(project)}").then((r) => r.json()).then((s) => ({ draft: !!s.draft, job: s.job?.status }))`);
  results.ok = true;
} catch (err) {
  results.ok = false;
  results.error = String(err?.stack || err);
  try { await shot("zz_failure"); } catch { /* gone */ }
} finally {
  try { browser.kill(); } catch { /* gone */ }
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* locked */ }
  console.log(JSON.stringify(results));
  process.exit(0);
}
