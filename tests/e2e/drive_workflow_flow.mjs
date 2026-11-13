// The main workflow with the Artwork editor in its one consistent place (below Issues on the current stage),
// in a headless Chromium browser (Chrome or Edge) through the DevTools protocol.  No npm packages: Node >= 22.
//
//   node tests/e2e/drive_workflow_flow.mjs <app url> <browser exe> <sailboat image> <screenshot dir>
//
// Import (artwork first) -> Plan screen: one primary next action at the top -> edit the artwork (unsaved, the
// next action waits for Save) -> Save -> Plan sheets -> Review -> back to editing from Review (unsaved edit),
// Plan's "Edit artwork" reaches the same editor with the edit -> reload recovers it -> Save: nothing re-planned,
// both headers offer planning from the edited artwork -> reload: history kept, undo works.  Prints one JSON line.
// (Planning from the edited artwork starts a new edit history on the planned artwork: the review's history holds
// only the edit made in Review.)
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const [url, browserExe, imagePath, shotDir] = process.argv.slice(2);
const results = { steps: {}, console_errors: [], dialogs: [] };
mkdirSync(shotDir, { recursive: true });
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const PORTHOLES = [[352, 210], [352, 250]];  // sailboat at scale 1 (as tests/e2e/drive_artwork.mjs)
const SUN_HEX = "#F9C832";

const profile = mkdtempSync(join(tmpdir(), "lf-e2e-"));
const browser = spawn(browserExe, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--disable-sync",
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
let session;
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, fail } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result);
  } else if (msg.method === "Page.javascriptDialogOpening") {
    results.dialogs.push({ type: msg.params.type, message: msg.params.message.split("\n")[0] });
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

async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(`${expression.slice(0, 160)}: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
  return r.result.value;
}
async function waitFor(expression, what, ms = 60000) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    try { if (await evaluate(expression)) return; } catch { /* rendering */ }
    await sleep(200);
  }
  throw new Error(`timed out waiting for: ${what}`);
}
const text = (sel) => evaluate(`document.querySelector(${JSON.stringify(sel)})?.innerText ?? ""`);
const idle = `!document.querySelector('[data-testid="job-banner"]')`;
async function clickSel(selector) {
  const ok = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el || el.disabled) return false; el.scrollIntoView({block: "center"}); el.click(); return true; })()`);
  if (!ok) throw new Error(`cannot click ${selector}: ${await evaluate(`document.querySelector(${JSON.stringify(selector)})?.outerHTML?.slice(0, 300) ?? "not found"`)}`);
}
async function shot(name) {
  await sleep(400);
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, "base64"));
}
async function artClick([row, col]) {
  await evaluate(`document.querySelector("#art-viewport").scrollIntoView({block: "center"})`);
  await sleep(200);
  const r = await evaluate(`(() => { const i = document.querySelector("#art-image"); const b = i.getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height, nw: i.naturalWidth, nh: i.naturalHeight}; })()`);
  const p = { x: r.x + ((col + 0.5) / r.nw) * r.w, y: r.y + ((row + 0.5) / r.nh) * r.h };
  for (const [type, buttons] of [["mouseMoved", 0], ["mousePressed", 1], ["mouseReleased", 0]]) {
    await send("Input.dispatchMouseEvent", { type, x: p.x, y: p.y, button: type === "mouseMoved" ? "none" : "left", buttons, clickCount: type === "mouseMoved" ? 0 : 1 });
  }
}
const unsavedIs = (n) => (n ? `(document.querySelector('[data-testid="art-unsaved"]')?.innerText || "").startsWith("${n} unsaved change")`
  : `document.querySelector('[data-testid="art-unsaved"]')?.innerText === "All changes saved"`);
const api = () => evaluate(`fetch("/api/projects/" + location.hash.split("/")[1]).then((r) => r.json()).then((s) => ({ proposal_seconds: s.proposal?.seconds ?? null, status: s.proposal?.status, draft: Boolean(s.draft), edits: s.artwork?.edits?.length ?? 0, unsaved: s.artwork?.unsaved?.count ?? 0, differs: Boolean(s.artwork?.differs_from_planned), job: s.job?.kind ?? null }))`);
// What a first-time user sees: the current step, the next action and whether it is on screen without scrolling,
// the filled (primary) buttons in the page body, and which big sections are open.
const screen = () => evaluate(`(() => {
  scrollTo(0, 0);
  const next = document.querySelector('[data-testid="plan-next-button"]');
  const r = next?.getBoundingClientRect();
  const visible = (e) => e.checkVisibility() && e.getBoundingClientRect().width > 0;
  const art = document.querySelector('[data-testid="artwork-panel"]');
  const issues = document.querySelector('[data-testid="issue-inspector"]');
  return {
    hash: location.hash, step: document.querySelector(".wf-step.current")?.dataset.step ?? null,
    next: next?.innerText.trim() ?? null, next_disabled: next ? next.disabled ?? next.hasAttribute("disabled") : null,
    next_on_first_screen: Boolean(r && r.bottom <= innerHeight && r.top >= 0),
    primaries: [...document.querySelectorAll("#tab-body .primary")].filter(visible).map((e) => e.innerText.trim()),
    edit_artwork: [...document.querySelectorAll('[data-testid="edit-artwork"]')].filter(visible).map((e) => e.innerText.trim()),
    artwork_panel: art ? { open: art.open, after_issues: Boolean(issues && issues.nextElementSibling === art) } : null,
    issues_open: issues ? issues.open ?? null : null,
    art_images: [...document.querySelectorAll("#tab-body img")].filter(visible).filter((i) => /artwork|target|intake/.test(i.src)).length,
  };
})()`);

try {
  // 1. Import, artwork first
  await send("Page.navigate", { url });
  await waitFor(`document.querySelector("#file-input")`, "home");
  const doc = await send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#file-input" });
  await send("DOM.setFileInputFiles", { nodeId, files: [resolve(imagePath)] });
  await waitFor(`document.querySelector("#upload-preview")`, "upload preview");
  await evaluate(`(() => { const el = document.querySelector("#f-scale"); el.value = "1"; el.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  await clickSel('[data-testid="plan-later"]');
  await clickSel('[data-action="create"]');
  await waitFor(`${idle} && document.querySelector('[data-testid="plan-next-button"]') && document.querySelector("#art-image")?.naturalWidth > 0`, "artwork prepared", 180000);
  results.steps.imported = await screen();
  await shot("f1_plan_not_planned");

  // 2. Edit the artwork: an unsaved change holds the next action until Save
  await artClick(PORTHOLES[0]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "selection");
  await clickSel(`[data-testid="art-palette-color"][data-color="${SUN_HEX}"]`);
  await waitFor(unsavedIs(1), "unsaved change");
  results.steps.unsaved = await screen();
  await shot("f2_unsaved_edit");
  await clickSel('[data-testid="art-save"]');
  await waitFor(`${unsavedIs(0)} && !document.querySelector('[data-testid="plan-next-button"]').disabled`, "saved");
  results.steps.saved = { ...(await screen()), api: await api() };

  // 3. Plan sheets (the primary next action at the top)
  await clickSel('[data-testid="plan-next-button"]');
  await waitFor(`document.querySelector('[data-testid="job-banner"]')`, "planning started", 15000);
  await waitFor(`${idle} && document.querySelector('[data-testid="plan-next-button"]')?.innerText.includes("Review this candidate")`, "planned", 300000);
  results.steps.planned = { ...(await screen()), api: await api() };
  await shot("f3_planned");

  // 4. Review
  await clickSel('[data-testid="plan-next-button"]');
  await waitFor(`${idle} && document.querySelectorAll('[data-testid="layer-row"]').length > 1 && document.querySelector('[data-testid="artwork-panel"]')`, "review", 300000);
  results.steps.review = await screen();
  await shot("f4_review");

  // 5. Back to editing from Review: the editor opens in place (below Issues), the edit stays unsaved
  await clickSel('.review-bar [data-testid="edit-artwork"]');
  await waitFor(`document.querySelector('[data-testid="artwork-panel"]').open && document.querySelector("#art-image")?.naturalWidth > 0`, "editor opened from Review");
  await sleep(900);  // smooth scroll
  const inView = await evaluate(`(() => { const r = document.querySelector('[data-testid="artwork-panel"]').getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; })()`);
  results.steps.edit_from_review = { ...(await screen()), in_view: inView };
  await artClick(PORTHOLES[1]);
  await waitFor(`document.querySelector('[data-testid="art-selection"]')`, "second selection");
  await clickSel(`[data-testid="art-palette-color"][data-color="${SUN_HEX}"]`);
  await waitFor(unsavedIs(1), "unsaved change in Review");
  await shot("f5_editing_from_review");
  // Plan's "Edit artwork" reaches the same editor (Review, below Issues) with the unsaved change
  await clickSel('[data-step="plan"]');
  await waitFor(`location.hash.endsWith("/proposal") && document.querySelector('[data-testid="plan-next"]')`, "plan screen");
  results.steps.plan_with_unsaved = await screen();
  await shot("f6_plan_with_unsaved");
  await clickSel('[data-testid="plan-next"] [data-testid="edit-artwork"]');
  await waitFor(`location.hash.endsWith("/review") && document.querySelector('[data-testid="artwork-panel"]')?.open && ${unsavedIs(1)}`, "same editor from Plan");
  results.steps.edit_from_plan = await screen();

  // 6. Reload: the unsaved change is recovered; Save: nothing is re-planned, planning is offered
  await evaluate("window.__beforeReload = true");
  await send("Page.reload");
  await waitFor(`!window.__beforeReload && document.querySelector('[data-testid="artwork-panel"]') && ${idle} && ${unsavedIs(1)}`, "recovered after reload");
  results.steps.after_reload = { unsaved_text: await text('[data-testid="art-unsaved"]'), api: await api() };
  await evaluate(`(() => { const p = document.querySelector('[data-testid="artwork-panel"]'); if (!p.open) p.querySelector("summary").click(); })()`);
  await clickSel('[data-testid="art-save"]');
  await waitFor(`${unsavedIs(0)} && document.querySelector('[data-testid="review-replan"]')`, "saved in Review");
  await sleep(1500);
  results.steps.saved_in_review = { ...(await screen()), api: await api() };
  await shot("f7_saved_not_replanned");
  await clickSel('[data-step="plan"]');
  await waitFor(`location.hash.endsWith("/proposal") && document.querySelector('[data-testid="plan-next-button"]')`, "plan after save");
  results.steps.plan_after_save = await screen();
  await shot("f8_plan_offers_replan");

  // 7. Reload: saved history kept; undo the last saved edit
  await evaluate("window.__beforeReload = true");
  await send("Page.reload");
  await waitFor(`!window.__beforeReload && document.querySelector('[data-testid="plan-next-button"]') && ${idle}`, "reloaded");
  results.steps.reloaded = { ...(await screen()), api: await api() };
  await clickSel('[data-testid="plan-next"] [data-testid="edit-artwork"]');
  await waitFor(`document.querySelector('[data-testid="artwork-panel"]')?.open && document.querySelectorAll('[data-testid="art-history"] li').length === 1`, "history after reload");
  results.steps.history_after_reload = await evaluate(`document.querySelectorAll('[data-testid="art-history"] li').length`);
  await clickSel('[data-testid="art-undo"]');
  await waitFor(`!document.querySelectorAll('[data-testid="art-history"] li').length && ${idle}`, "undo saved edit");
  await sleep(1000);
  results.steps.after_undo = { ...(await screen()), api: await api() };
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
