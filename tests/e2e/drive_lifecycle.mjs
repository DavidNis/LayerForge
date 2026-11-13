// Drives the app's lifetime in a headless Chromium browser (Edge or Chrome) through the DevTools protocol:
// tabs check in, closing one of two tabs or reloading keeps the app running, the last tab closing stops it
// (after a running job has finished), a killed browser is noticed, and Exit stops it.  No npm packages:
// Node >= 22 (built-in WebSocket).  The app runs under the real launcher (tests/test_app_lifecycle.py).
//
//   node tests/e2e/drive_lifecycle.mjs <app url> <browser exe> <scenario: tabs | crash | sleep | exit> <screenshot dir>
//
// Whether the app still runs is checked with a bare TCP connect (no HTTP request, so it is not "activity").
// At checkpoints the driver prints "@@<name>" and waits for one line on stdin (the test acts in between).
// Prints one JSON line at the end.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";

const [url, browserExe, scenario, shotDir] = process.argv.slice(2);
const results = { scenario, steps: [], console_errors: [] };
mkdirSync(shotDir, { recursive: true });
const port = Number(new URL(url).port);
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const stdinLines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
async function checkpoint(name) {
  console.log(`@@${name}`);
  await stdinLines.next();
}

const alive = () => new Promise((ok) => {
  const socket = connect({ host: "127.0.0.1", port });
  socket.once("connect", () => { socket.destroy(); ok(true); });
  socket.once("error", () => ok(false));
});
async function stoppedWithin(ms) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    if (!(await alive())) return (Date.now() - start) / 1000;
    await sleep(250);
  }
  return null;
}

const profile = mkdtempSync(join(tmpdir(), "lf-e2e-"));
const browser = spawn(browserExe, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--window-size=1280,900", "about:blank",
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
const dialogs = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, fail } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result);
  } else if (msg.method === "Page.javascriptDialogOpening") {
    dialogs.push({ type: msg.params.type, message: msg.params.message });
    send("Page.handleJavaScriptDialog", { accept: true }, msg.sessionId).catch(() => {});
  } else if (msg.method === "Runtime.exceptionThrown") {
    results.console_errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  }
});
ws.addEventListener("close", () => { for (const { fail } of waiting.values()) fail(new Error("browser connection closed")); });
const send = (method, params = {}, sessionId) => new Promise((ok, fail) => {
  const id = nextId++;
  waiting.set(id, { ok, fail });
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});

async function openTab() {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Page.navigate", { url }, sessionId);
  const tab = { targetId, sessionId };
  await until(tab, `!document.querySelector("#app-exit")?.hidden`, 30000, "Exit shown in the top bar");
  return tab;
}
async function evaluate(tab, expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, tab.sessionId);
  return r.result.value;
}
async function until(tab, expression, ms, what) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    try { if (await evaluate(tab, expression)) return; } catch { /* page navigating */ }
    await sleep(200);
  }
  throw new Error(`timed out waiting for: ${what}`);
}
async function shot(tab, name) {
  const { data } = await send("Page.captureScreenshot", { format: "png" }, tab.sessionId);
  writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, "base64"));
}
const step = (name, fields) => { results.steps.push({ name, ...fields }); console.error(`[drive] ${name} ${JSON.stringify(fields)}`); };

try {
  if (scenario === "tabs") {
    const a = await openTab();
    const b = await openTab();
    step("two_tabs_open", { exit_label: await evaluate(a, `document.querySelector("#app-exit").textContent.trim()`) });
    await shot(a, "lifecycle_topbar_exit");
    await send("Target.closeTarget", { targetId: a.targetId });
    await sleep(8000);  // well beyond the grace period
    step("closed_one_of_two", { alive: await alive() });
    await send("Page.reload", {}, b.sessionId);
    await until(b, `!document.querySelector("#app-exit")?.hidden`, 30000, "page back after reload");
    await sleep(8000);
    step("after_reload", { alive: await alive() });
    await evaluate(b, `location.hash = "#/"; true`);
    await sleep(1000);
    await checkpoint("job");  // the test starts a job that runs until it is released
    await send("Target.closeTarget", { targetId: b.targetId });
    await sleep(15000);
    step("last_tab_closed_while_job_runs", { alive: await alive() });
    await checkpoint("release");  // the job finishes now
    step("after_job_finished", { stopped_after_s: await stoppedWithin(20000) });
  } else if (scenario === "crash") {
    await openTab();
    await sleep(1500);
    browser.kill("SIGKILL");  // no pagehide, no beacon: only the heartbeats stop
    const t0 = Date.now();
    await sleep(5000);
    step("five_s_after_crash", { alive: await alive() });
    const stopped = await stoppedWithin(60000);
    step("after_crash", { crash_to_stop_s: stopped === null ? null : Math.round((Date.now() - t0) / 100) / 10 });
  } else if (scenario === "sleep") {
    const a = await openTab();
    await sleep(1000);
    await send("Page.setWebLifecycleState", { state: "frozen" }, a.sessionId);  // fires the page's freeze event
    await sleep(15000);
    step("frozen_beyond_stale", { alive: await alive() });
    await send("Page.setWebLifecycleState", { state: "active" }, a.sessionId);  // fires resume
    await sleep(3000);
    step("woken", { alive: await alive(),
                    stopped_page: await evaluate(a, `!!document.querySelector("[data-testid=app-stopped]")`) });
    await send("Target.closeTarget", { targetId: a.targetId });
    await sleep(1500);
  } else if (scenario === "exit") {
    const a = await openTab();
    const b = await openTab();
    await evaluate(a, `document.querySelector("#app-exit").click(); true`);
    await until(a, `!!document.querySelector("[data-testid=app-stopped]")`, 20000, "stopped page after Exit");
    await shot(a, "lifecycle_after_exit");
    step("exit_clicked", { dialogs: dialogs.map((d) => d.message.split("\n")[0]), stopped_after_s: await stoppedWithin(20000) });
    await until(b, `!!document.querySelector("[data-testid=app-stopped]")`, 30000, "other tab notices the stop");
    await shot(b, "lifecycle_other_tab_after_exit");
    step("other_tab", { shows_stopped: true });
  }
  results.ok = true;
} catch (err) {
  results.ok = false;
  results.error = String(err?.stack || err);
} finally {
  try { browser.kill(); } catch { /* already gone */ }
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* still locked */ }
  console.log(JSON.stringify(results));
  process.exit(0);
}
