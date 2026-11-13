// First-time-user tour of the whole workflow in a headless Chromium browser (Edge or Chrome), saving one
// screenshot per stage so UI changes can be compared before / after.  No npm packages: Node >= 22.
//
//   node tests/e2e/drive_ux_tour.mjs <app url> <browser exe> <image> <screenshot dir> [window width, default 1440]
//
// Import (artwork first) -> plan -> compare / notes -> review -> issue detail -> printability -> export ->
// kit -> physical size analysis -> accept.  Waits for jobs through the API.  Prints one JSON line with the
// visible text of key places (for text assertions) and console errors.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, browserExe, imagePath, shotDir, widthArg] = process.argv.slice(2);
const WIDTH = Number(widthArg) || 1440;
const results = { shots: [], text: {}, console_errors: [] };
mkdirSync(shotDir, { recursive: true });
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

const profile = mkdtempSync(join(tmpdir(), "lf-e2e-"));
const browser = spawn(browserExe, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--disable-sync",
  "--no-default-browser-check", "--disable-extensions", `--window-size=${WIDTH},1000`, "about:blank",
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
let session;
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, fail } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result);
  } else if (msg.method === "Page.javascriptDialogOpening") {
    send("Page.handleJavaScriptDialog", { accept: true }).catch(() => {});
  } else if (msg.method === "Runtime.exceptionThrown") {
    results.console_errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  } else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
    results.console_errors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));
  }
});
const send = (method, params = {}, sessionId = session) => new Promise((ok, fail) => {
  const id = nextId++;
  waiting.set(id, { ok, fail });
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const { targetId } = await send("Target.createTarget", { url: "about:blank" }, null);
session = (await send("Target.attachToTarget", { targetId, flatten: true }, null)).sessionId;
await send("Page.enable");
await send("Runtime.enable");
await send("DOM.enable");

async function js(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(`${expression.slice(0, 120)}: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
  return r.result.value;
}
async function until(expression, ms, what) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    try { if (await js(expression)) return; } catch { /* rendering */ }
    await sleep(250);
  }
  throw new Error(`timed out waiting for: ${what}`);
}
async function shot(name, { full = false, selector = null } = {}) {
  await sleep(500);
  const params = { format: "png" };
  if (selector) {
    const box = await js(`(() => { const e = ${selector}; e.scrollIntoView({ block: "start" }); window.scrollBy(0, -70);
      const r = e.getBoundingClientRect(); return { x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: Math.min(r.height, 2400) }; })()`);
    await sleep(300);
    Object.assign(params, { captureBeyondViewport: true, clip: { x: 0, y: Math.max(0, box.y - 70), width: WIDTH, height: box.h + 90, scale: 1 } });
  } else if (full) {
    const m = await send("Page.getLayoutMetrics");
    const s = m.cssContentSize || m.contentSize;
    Object.assign(params, { captureBeyondViewport: true, clip: { x: 0, y: 0, width: s.width, height: Math.min(s.height, 7000), scale: 1 } });
  } else {
    await js("window.scrollTo(0, 0)");
  }
  const { data } = await send("Page.captureScreenshot", params);
  writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, "base64"));
  results.shots.push(name);
}
const project = () => js("decodeURIComponent(location.hash.split('/')[1] || '')");
async function waitJob(ms = 600000) {
  const name = await project();
  const start = Date.now();
  while (Date.now() - start < ms) {
    const state = await (await fetch(`${url}api/projects/${encodeURIComponent(name)}`)).json();
    if (state.job && !["queued", "running"].includes(state.job.status)) {
      if (state.job.status === "failed") throw new Error(`job ${state.job.kind} failed: ${state.job.error?.message}`);
      await sleep(1500);  // the page polls and renders the result
      return state;
    }
    await sleep(700);
  }
  throw new Error("job did not finish");
}
const click = (selector) => js(`(() => { const e = ${selector}; if (!e) throw new Error("not found: " + ${JSON.stringify(selector)}); e.click(); return true; })()`);
const button = (re, root = "document") => `[...${root}.querySelectorAll("button, a.button, a")].find((b) => ${re}.test(b.textContent.trim()) && !b.disabled)`;
const summary = (re) => `[...document.querySelectorAll("summary")].find((s) => ${re}.test(s.textContent))`;
const openSection = (re) => js(`(() => { const s = ${summary(re)}; if (!s) throw new Error("no section ${re}"); if (!s.parentElement.open) s.click(); return true; })()`);
const text = (selector) => js(`(${selector})?.innerText || ""`);
const step = (name) => console.error(`[tour] ${name}`);

try {
  await send("Page.navigate", { url });
  await until(`!!document.querySelector("input[type=file]")`, 30000, "home page");
  await shot("01_home");
  step("import");
  const input = await send("Runtime.evaluate", { expression: 'document.querySelector("input[type=file]")' });
  await send("DOM.setFileInputFiles", { files: [imagePath], objectId: input.result.objectId });
  await js(`(() => { const c = document.querySelector("#app input[type=checkbox]"); if (!c.checked) c.click(); return true; })()`);
  await sleep(300);
  await click(button(/^Import/));
  await until(`location.hash.split("/").length > 2 || /#\\/[^/]+/.test(location.hash)`, 30000, "project page");
  await waitJob();
  await shot("02_artwork_stage", { full: true });
  results.text.stepper_artwork = await text(`document.querySelector(".stepper, .steps, [data-testid=stepper]")`);
  results.text.artwork_page = await text(`document.querySelector("#app")`);

  step("plan");
  await click(button(/Plan sheets from this artwork/));
  await sleep(1800);
  await shot("03_planning_progress");
  results.text.planning_progress = await text(`document.querySelector("#app")`);
  await waitJob();
  await shot("04_planned", { full: true });
  results.text.planned = await text(`document.querySelector("#app")`);

  step("compare");
  await openSection(/Compare candidates/i);
  await openSection(/^\s*Notes/i);
  await shot("05_compare_notes", { selector: `${summary(/Compare candidates/i)}.closest(".panel") || ${summary(/Compare candidates/i)}.parentElement` });
  results.text.compare = await text(`${summary(/Compare candidates/i)}.parentElement`);
  results.text.notes = await text(`${summary(/^\s*Notes/i)}.parentElement`);

  step("review");
  await click(`[...document.querySelectorAll("#app button.primary")].find((b) => /Review/.test(b.textContent))`);
  await waitJob();
  await shot("06_review", { full: true });
  results.text.review = await text(`document.querySelector("#app")`);

  step("issue");
  await click(`[...document.querySelectorAll("#app [data-action]")].find((e) => /Opening may close/.test(e.textContent) && e.children.length < 6)`);
  await sleep(800);
  await shot("07_issue_detail", { selector: `${summary(/^\s*Issues/)}.parentElement` });
  results.text.issue = await text(`${summary(/^\s*Issues/)}.parentElement`);

  step("printability");
  await js(`location.hash = location.hash.replace(/\\/[^/]*$/, "/printability"); true`);
  await sleep(1200);
  await click(button(/^Run estimate|^Check printability$|^Run check/));
  await waitJob();
  await shot("08_printability", { full: true });
  results.text.printability = await text(`document.querySelector("#app")`);

  step("export");
  await js(`location.hash = location.hash.replace(/\\/[^/]*$/, "/kit"); true`);
  await sleep(1200);
  await shot("09_export");
  results.text.export = await text(`document.querySelector("#app")`);
  await click(button(/^Build print kit/));
  await sleep(1500);
  await shot("10_kit_progress");
  results.text.kit_progress = await text(`document.querySelector("#app")`);
  await waitJob();
  await shot("11_kit_ready", { full: true });
  results.text.kit = await text(`document.querySelector("#app")`);

  step("physical size");
  await js(`location.hash = location.hash.replace(/\\/[^/]*$/, "/proposal"); true`);
  await sleep(1500);
  await shot("12_plan_after_review", { full: true });
  results.text.plan_after_review = await text(`document.querySelector("#app")`);
  await openSection(/Physical size/);
  await click(button(/Check printability at other sizes/));
  await waitJob();
  await openSection(/Physical size/);
  await shot("13_physical_size", { selector: `${summary(/Physical size/)}.parentElement` });

  step("accept");
  await click(button(/Accept this plan/));
  await sleep(4000);
  const accepted = `[...document.querySelectorAll("[data-testid=accepted-plan], details")].find((d) => /Accepted plan|Previous attempts/.test(d.querySelector("summary")?.textContent || d.textContent))`;
  await js(`(() => { const d = ${accepted}; if (d && d.tagName === "DETAILS" && !d.open) d.querySelector("summary").click(); return true; })()`);
  await shot("14_accepted", { selector: accepted });
  results.text.accepted = await text(accepted);
  results.text.footer = await text(`document.querySelector("#footer")`);

  step("switch candidate");  // the other candidate (if any): Review shows the switch; then the widening panel
  const other = await js(`fetch("/api/projects/" + location.hash.split("/")[1]).then((r) => r.json()).then((st) => (st.proposal?.candidates || []).find((c) => c.status === "VALID" && c.name !== st.record.candidate)?.name || null)`);
  if (other) {
    await openSection(/Compare candidates/i);
    await shot("15_compare_candidates", { selector: `${summary(/Compare candidates/i)}.parentElement` });
    await click(`document.querySelector('[data-testid="candidates-section"] tr[data-candidate="${other}"] [data-testid="candidate-review"]')`);
    await waitJob();
    await until(`!!document.querySelector('[data-testid="review-bar"]')`, 30000, "review after switch");
    await shot("16_switched");
    results.text.switched = await text(`document.querySelector('[data-testid="review-bar"]')`);
  }
  await click(`document.querySelector('[data-step="review"]')`);
  await sleep(1500);
  await openSection(/Cutout widening/);
  await shot("17_widening", { selector: `${summary(/Cutout widening/)}.parentElement` });
  results.text.widening = await text(`${summary(/Cutout widening/)}.parentElement`);
  results.ok = true;
} catch (err) {
  results.ok = false;
  results.error = String(err?.stack || err);
  try { await shot("zz_failure", { full: true }); } catch { /* browser gone */ }
} finally {
  try { browser.kill(); } catch { /* gone */ }
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* locked */ }
  console.log(JSON.stringify(results));
  process.exit(0);
}
