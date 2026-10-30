// Drives the real LayerForge app UI in a headless Chromium browser (Edge or Chrome)
// through the DevTools protocol: real clicks, a real file upload and real pointer
// drags on the edit canvas.  No npm packages: Node >= 22 (built-in WebSocket).
//
//   node tests/e2e/drive_app.mjs <app url> <browser exe> <image> <screenshot dir>
//
// Prints one JSON line with the observed results; exits 1 on the first failed step.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const [url, browserExe, imagePath, shotDir] = process.argv.slice(2);
const results = { steps: [] };
mkdirSync(shotDir, { recursive: true });

// ------------------------------------------------------------------ browser + CDP
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
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, fail } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? fail(new Error(JSON.stringify(msg.error))) : ok(msg.result);
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

// ---------------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`evaluate failed: ${expression}: ${r.exceptionDetails.exception?.description}`);
  return r.result.value;
}
async function waitFor(expression, what, timeoutMs = 60000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(250);
  }
  await shot(`timeout_${what.replace(/\W+/g, "_")}`);
  throw new Error(`timed out waiting for: ${what}`);
}
const text = (sel) => evaluate(`document.querySelector(${JSON.stringify(sel)})?.innerText ?? ""`);
const bodyHas = (s) => `document.body.innerText.includes(${JSON.stringify(s)})`;
const idle = `!document.querySelector('[data-testid="job-banner"]')`;
async function clickSel(selector) {
  const ok = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el || el.disabled) return false; el.scrollIntoView({block: "center"}); el.click(); return true; })()`);
  if (!ok) throw new Error(`cannot click ${selector}`);
}
async function clickText(tag, label) {
  const ok = await evaluate(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(tag)})].find((e) => e.innerText.trim().startsWith(${JSON.stringify(label)}) && !e.disabled); if (!el) return false; el.scrollIntoView({block: "center"}); el.click(); return true; })()`);
  if (!ok) throw new Error(`no enabled ${tag} "${label}"`);
}
async function setValue(selector, value) {
  await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.value = ${JSON.stringify(String(value))}; el.dispatchEvent(new Event("change", {bubbles: true})); })()`);
}
async function canvasPoint(mm) {
  await evaluate(`document.querySelector("#overlay").scrollIntoView({block: "center"})`);
  await sleep(150);
  const r = await evaluate(`(() => { const b = document.querySelector("#overlay").getBoundingClientRect(); return {x: b.left, y: b.top, w: b.width, h: b.height}; })()`);
  return { x: r.x + (mm[0] / 225) * r.w, y: r.y + (1 - mm[1] / 225) * r.h };
}
async function mouse(type, p, buttons = 1) {
  await send("Input.dispatchMouseEvent", { type, x: p.x, y: p.y, button: "left", buttons, clickCount: type === "mouseMoved" ? 0 : 1 });
}
async function canvasClick(mm) {
  const p = await canvasPoint(mm);
  await mouse("mouseMoved", p, 0);
  await mouse("mousePressed", p);
  await mouse("mouseReleased", p, 0);
}
async function canvasDrag(from, to) {
  const a = await canvasPoint(from);
  const b = await canvasPoint(to);
  await mouse("mouseMoved", a, 0);
  await mouse("mousePressed", a);
  for (let i = 1; i <= 5; i++) await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / 5, y: a.y + ((b.y - a.y) * i) / 5 });
  await mouse("mouseReleased", b, 0);
}
async function shot(name) {
  await evaluate("window.scrollTo(0, 0)");  // the sticky header would otherwise be drawn mid-page
  await sleep(100);
  const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, "base64"));
}
function step(name, detail = {}) {
  results.steps.push({ name, ...detail });
  console.error(`[e2e] ${name} ${JSON.stringify(detail)}`);
}
const physicalText = () => text('[data-testid="status-physical"]');

// ------------------------------------------------------------------------- flow
try {
  await send("Page.navigate", { url });
  await waitFor(`document.querySelector("#dropzone")`, "import view");
  step("import view shown");

  // 1. import + L1 proposal
  const doc = await send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#file-input" });
  await send("DOM.setFileInputFiles", { nodeId, files: [resolve(imagePath)] });
  await waitFor(`document.querySelector("#upload-preview")`, "upload preview");
  await setValue("#f-scale", "1");
  await setValue("#f-name", "sailboat e2e");
  await shot("01_import");
  await clickSel('[data-action="create"]');
  await waitFor(`document.querySelector('[data-testid="job-banner"]')`, "proposal job banner", 15000);
  step("proposal job running", { banner: (await text('[data-testid="job-banner"]')).split("\n")[0] });
  await waitFor(`${bodyHas("Ready for review")} && ${idle}`, "proposal ready", 300000);
  results.physical_after_proposal = await physicalText();
  await shot("02_proposal");
  step("proposal ready", { hash: await evaluate("location.hash") });

  // 2. start the review with the selected candidate
  await clickText("button", "Review this candidate");
  await waitFor(`document.querySelectorAll('[data-testid="layer-row"]').length > 1 && ${idle}`, "review editor", 180000);
  const layers = await evaluate(`[...document.querySelectorAll('[data-testid="layer-row"]')].map((e) => e.querySelector(".name").innerText)`);
  results.layers_front_to_back = layers;
  await shot("03_review");
  step("review started", { layers });

  // 3a. an invalid request: fill a "cutout" where the front sheet has material -> actionable error
  await clickSel('[data-testid="layer-row"]');  // front sheet
  await clickSel('[data-tool="fill_hole"]');
  await canvasClick([60, 40]);
  await waitFor(`document.querySelector('[data-testid="pending-edit"]')`, "pending fill_hole");
  await clickSel('[data-action="apply-pending"]');
  await waitFor(`document.querySelector(".banner.error") && ${idle}`, "error banner", 60000);
  results.error_banner = await text(".banner.error");
  await shot("04_error");
  step("invalid edit reported", { banner: results.error_banner.split("\n").slice(0, 2).join(" | ") });
  await clickSel('[data-action="dismiss-error"]');

  // 3b. cut a 0.6 mm slot in the front sheet (a visible change -> DRAFT_INVALID)
  await clickSel('[data-tool="subtract_bar"]');
  await setValue("#bar-width", "0.6");
  await canvasDrag([40, 40], [100, 40]);
  await waitFor(`document.querySelector('[data-testid="pending-edit"]')`, "pending slot");
  results.pending_slot = await text('[data-testid="pending-edit"]');
  await clickSel('[data-action="apply-pending"]');
  await waitFor(`${bodyHas("Edit #1")} && ${idle}`, "slot applied", 180000);
  results.after_slot = {
    validation: await text('[data-testid="validation-panel"] h3'),
    change: await text('[data-testid="change-panel"] h3'),
    geometry_card: await text('[data-testid="status-geometry"]'),
  };
  await clickSel('[data-action="view"][data-view="changes_previous"]');
  await shot("05_visible_change_invalid");
  step("slot cut", results.after_slot);

  // 3c. accept the intended visible change -> valid again
  await clickText("button", "Accept front view as artwork");
  await waitFor(`document.querySelector('[data-testid="pending-edit"]')`, "pending accept");
  await clickSel('[data-action="apply-pending"]');
  await waitFor(`${bodyHas("Edit #2")} && ${idle}`, "accept applied", 180000);
  results.after_accept = await text('[data-testid="validation-panel"] h3');
  step("artwork accepted", { validation: results.after_accept });

  // 3d. optional rim on the back sheet only, undo it, then apply it to all layers
  const rimRows = `[...document.querySelectorAll('[data-testid="layer-row"]')].filter((r) => r.innerText.includes("rim 6")).length`;
  await evaluate(`(() => { const rows = document.querySelectorAll('[data-testid="layer-row"]'); rows[rows.length - 1].click(); })()`);
  await waitFor(`document.querySelector("#p-rim")`, "sheet panel");
  results.rim_all_default = await evaluate(`document.querySelector("#p-rim-all").checked`);
  await evaluate(`(() => { const box = document.querySelector("#p-rim-all"); box.checked = false; box.dispatchEvent(new Event("change", { bubbles: true })); })()`);
  await evaluate(`document.querySelector("#p-rim").checked = true`);
  await setValue("#p-rim-w", "6");
  await setValue("#p-rim-h", "2.4");
  await clickSel('[data-action="rim"]');
  await waitFor(`${bodyHas("Edit #3")} && ${idle}`, "rim applied", 180000);
  results.rim_layer = (await evaluate(`[...document.querySelectorAll('[data-testid="layer-row"]')].pop().innerText`));
  results.rim_single_rows = await evaluate(rimRows);
  await clickSel('[data-action="undo"]');
  await waitFor(`${bodyHas("undo: 3 → 2 edits")} && ${idle}`, "undo", 180000);
  results.after_undo_layer = (await evaluate(`[...document.querySelectorAll('[data-testid="layer-row"]')].pop().innerText`));
  step("rim undone", { before: results.rim_layer.replace(/\n/g, " "), after: results.after_undo_layer.replace(/\n/g, " ") });
  await evaluate(`(() => { const rows = document.querySelectorAll('[data-testid="layer-row"]'); rows[rows.length - 1].click(); })()`);
  await evaluate(`(() => { const box = document.querySelector("#p-rim-all"); box.checked = true; box.dispatchEvent(new Event("change", { bubbles: true })); })()`);
  await evaluate(`document.querySelector("#p-rim").checked = true`);
  await setValue("#p-rim-w", "6");
  await setValue("#p-rim-h", "2.4");
  await clickSel('[data-action="rim"]');
  await waitFor(`${bodyHas("Edit #3")} && ${idle}`, "rim re-applied to all layers", 180000);
  results.layer_rows = await evaluate(`document.querySelectorAll('[data-testid="layer-row"]').length`);
  results.rim_all_rows = await evaluate(rimRows);
  step("rim on all layers", { rows: results.layer_rows, with_rim: results.rim_all_rows });

  // 3e. save/reload: verify replay, then reload the page from scratch
  await clickSel('[data-action="verify"]');
  await waitFor(`${bodyHas("reproduces the saved geometry exactly")} && ${idle}`, "verify replay", 180000);
  await send("Page.reload");
  await waitFor(`document.querySelectorAll('[data-testid="layer-row"]').length > 1`, "editor after reload", 60000);
  results.history_after_reload = await evaluate(`document.querySelectorAll(".history li").length`);
  results.rim_after_reload = (await evaluate(rimRows)) === results.layer_rows;
  await shot("06_review_after_reload");
  step("reloaded", { history: results.history_after_reload, rim: results.rim_after_reload });

  // 4. printability estimate of the draft
  await clickSel('[data-action="tab"][data-tab="printability"]');
  await clickSel('[data-action="run-printability"]');
  await waitFor(`document.querySelector('[data-testid="printability-result"]') && ${idle}`, "printability", 300000);
  results.printability = await text('[data-testid="printability-result"] h3');
  results.findings = await evaluate(`[...document.querySelectorAll('[data-testid="findings"] tbody tr')].map((r) => r.innerText.replace(/\\s+/g, " ").trim())`);
  results.fdm_card = await text('[data-testid="status-fdm"]');
  await shot("07_printability");
  step("printability", { status: results.printability, findings: results.findings.length });

  // 5. print kit
  await clickSel('[data-action="tab"][data-tab="kit"]');
  results.bambu_option_default = await evaluate(`document.querySelector("#kit-bambu")?.checked`);
  await evaluate(`(() => { const box = document.querySelector("#kit-bambu"); box.checked = true; box.dispatchEvent(new Event("change", { bubbles: true })); })()`);
  results.bambu_printer_enabled = await evaluate(`!document.querySelector("#kit-bambu-printer").disabled`);
  await clickSel('[data-action="build-kit"]');
  await waitFor(`document.querySelector('[data-testid="kit-card"]') && ${idle}`, "kit", 600000);
  // Completed-kit state: headline, one filled primary action, sections collapsed, warnings visible.
  const sectionsOpen = `[...document.querySelectorAll('section.kit-card [data-testid^="kit-section-"]')].map((d) => d.open)`;
  results.kit_ui = {
    title: await text('[data-testid="kit-title"]'),
    primary_buttons: await evaluate(`[...document.querySelectorAll('#tab-body .primary')].filter((e) => e.checkVisibility()).map((e) => e.innerText.trim())`),
    download_class: await evaluate(`document.querySelector('[data-testid="kit-download"]').className`),
    guide_href: await evaluate(`document.querySelector('[data-testid="kit-guide"]').getAttribute("href")`),
    sections: await evaluate(`[...document.querySelectorAll('section.kit-card [data-testid^="kit-section-"] > summary h3')].map((h) => h.innerText)`),
    sections_open_initially: await evaluate(sectionsOpen),
    warnings: await text('[data-testid="kit-warnings"]'),
    build_open: await evaluate(`Boolean(document.querySelector('[data-testid="kit-build"]'))`),
    new_button: await evaluate(`(() => { const b = document.querySelector('[data-testid="kit-new"]'), r = b.getBoundingClientRect(), c = document.querySelector("section.kit-card").getBoundingClientRect(); return { text: b.innerText.trim(), right: Math.round(c.right - r.right), above: r.bottom <= c.top }; })()`),
    technical_visible_before: await evaluate(`document.querySelector('[data-testid="kit-verify"]').checkVisibility()`),
  };
  await shot("08_kit");
  // Post-build actions: the zip and the guide are served; checksums in the Technical section.
  results.kit_ui.zip_status = await evaluate(`fetch(document.querySelector('[data-testid="kit-download"]').href).then((r) => r.status + " " + r.headers.get("content-type"))`);
  results.kit_ui.guide_status = await evaluate(`fetch(document.querySelector('[data-testid="kit-guide"]').href).then((r) => r.text()).then((t) => t.slice(0, 40))`);
  await clickSel('section.kit-card [data-testid="kit-section-technical"] > summary');
  await clickSel('[data-action="verify-kit"]');
  await waitFor(bodyHas("All files match checksums.sha256"), "kit checksums");
  for (const key of ["files", "assembly", "validation"]) await clickSel(`section.kit-card [data-testid="kit-section-${key}"] > summary`);
  results.kit_ui.sections_open_after = await evaluate(sectionsOpen);
  results.kit = {
    cad: await text('[data-testid="kit-cad"]'),
    fdm: await text('[data-testid="kit-fdm"]'),
    physical: await text('[data-testid="kit-physical"]'),
    zip: await evaluate(`document.querySelector('[data-testid="kit-card"] a[download]')?.getAttribute("href")`),
    geometry_card: await text('[data-testid="status-geometry"]'),
    bambu: await text('[data-testid="bambu-result"]'),
    bambu_href: await evaluate(`document.querySelector('[data-testid="bambu-result"] a[download]')?.getAttribute("href") || null`),
    file_links: await evaluate(`document.querySelectorAll('[data-testid="kit-files"] a[download]').length`),
  };
  results.physical_at_end = await physicalText();
  await shot("08b_kit_expanded");
  // The expanded state survives a re-render (kept per kit section).
  await clickSel('[data-action="reload"], [data-action="tab"][data-tab="kit"]');
  results.kit_ui.sections_open_after_render = await evaluate(sectionsOpen);
  for (const key of ["files", "assembly", "validation", "technical"]) await clickSel(`section.kit-card [data-testid="kit-section-${key}"] > summary`);

  // Narrow (phone) width: no horizontal page scroll, actions stacked full width.
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await sleep(300);
  results.kit_mobile = {
    overflow: await evaluate(`document.documentElement.scrollWidth - window.innerWidth`),
    download_width: await evaluate(`Math.round(document.querySelector('[data-testid="kit-download"]').getBoundingClientRect().width)`),
    actions_width: await evaluate(`Math.round(document.querySelector('[data-testid="kit-actions"]').getBoundingClientRect().width)`),
  };
  await shot("08c_kit_mobile");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  step("kit built", { cad: results.kit.cad.split("\n")[1], fdm: results.kit.fdm.split("\n")[1], title: results.kit_ui.title });

  // A second kit: the first moves to "Earlier kits"; delete it there (cancel first, then confirm).
  const firstKit = await evaluate(`document.querySelector('section.kit-card').dataset.kit`);
  await clickSel('[data-testid="kit-new"]');
  await shot("08i_kit_new_form_open");
  results.kit_delete_form_opened = await evaluate(`Boolean(document.querySelector('[data-testid="kit-build"] [data-action="build-kit"]')) && document.querySelector('[data-testid="kit-new"]').getAttribute("aria-expanded") === "true"`);
  await clickSel('[data-action="build-kit"]');
  await waitFor(`document.querySelector('section.kit-card')?.dataset.kit !== ${JSON.stringify(firstKit)} && ${idle}`, "second kit", 600000);
  const secondKit = await evaluate(`document.querySelector('section.kit-card').dataset.kit`);
  results.kit_delete = {
    first: firstKit, second: secondKit,
    form_closed_after_build: await evaluate(`!document.querySelector('[data-testid="kit-build"]')`),
    earlier_before: await evaluate(`[...document.querySelectorAll('.kit-older')].map((d) => d.dataset.kit)`),
  };
  const older = `.kit-older[data-kit="${firstKit}"]`;
  await clickSel(`${older} > summary`);
  await clickSel(`${older} [data-testid="kit-delete"]`);
  results.kit_delete.confirm_text = await text(`${older} [data-testid="kit-confirm-delete"]`);
  await shot("08d_kit_delete_confirm");
  await clickSel(`${older} [data-testid="kit-delete-cancel"]`);
  results.kit_delete.after_cancel = await evaluate(`Boolean(document.querySelector(${JSON.stringify(older)})) && !document.querySelector('[data-testid="kit-confirm-delete"]')`);
  await clickSel(`${older} [data-testid="kit-delete"]`);
  await clickSel(`${older} [data-testid="kit-delete-confirm"]`);
  await waitFor(`document.querySelector('[data-testid="kit-notice"]')`, "delete notice");
  results.kit_delete.notice = await text('[data-testid="kit-notice"]');
  results.kit_delete.cards_after = await evaluate(`[...document.querySelectorAll('[data-testid="kit-card"]')].map((d) => d.dataset.kit)`);
  await shot("08e_kit_after_delete");
  step("kit deleted", { notice: results.kit_delete.notice.split("\n")[0] });

  // Delete the whole project from its screen (cancel first, then confirm): back to the project list.
  const projectName = await text("h1");
  results.state_before_delete = await evaluate(`fetch("/api/projects/" + encodeURIComponent(${JSON.stringify(projectName)})).then((r) => r.json())`);
  await clickSel('[data-testid="project-delete"]');
  results.project_delete = { name: projectName, confirm_text: await text('[data-testid="project-confirm-delete"]') };
  await shot("09a_project_delete_confirm");
  await clickSel('[data-testid="project-delete-cancel"]');
  results.project_delete.after_cancel = await evaluate(`!document.querySelector('[data-testid="project-confirm-delete"]') && document.querySelector("h1").textContent === ${JSON.stringify(projectName)}`);
  await clickSel('[data-testid="project-delete"]');
  await clickSel('[data-testid="project-delete-confirm"]');
  await waitFor(`document.querySelector('[data-testid="project-notice"]')`, "project delete notice");
  results.project_delete.notice = await text('[data-testid="project-notice"]');
  results.project_delete.hash = await evaluate("location.hash");
  results.project_delete.import_ready = await evaluate(`Boolean(document.querySelector("#dropzone")) && Boolean(document.querySelector('[data-action="create"]'))`);
  results.project_delete.listed_after = await evaluate(`[...document.querySelectorAll('[data-testid="project-row"]')].map((r) => r.dataset.project)`);
  results.project_delete.in_select_after = await evaluate(`[...document.querySelectorAll("#project-select option")].map((o) => o.value).filter(Boolean)`);
  await shot("09b_project_deleted");
  step("project deleted", { notice: results.project_delete.notice.split("\n")[0] });

  // Delete another project (a failed proposal, created by the test) from the project list.
  const other = await evaluate(`document.querySelector('[data-testid="project-row"]')?.dataset.project ?? null`);
  if (other) {
    const row = `[data-testid="project-row"][data-project="${other}"]`;
    await clickSel(`${row} [data-testid="project-delete"]`);
    results.project_list_delete = { name: other, confirm_text: await text('[data-testid="project-confirm-delete"]') };
    await shot("09c_project_list_delete_confirm");
    await clickSel('[data-testid="project-delete-confirm"]');
    await waitFor(`!document.querySelector(${JSON.stringify(row)}) && document.querySelector('[data-testid="project-notice"]')`, "list delete");
    results.project_list_delete.notice = await text('[data-testid="project-notice"]');
    results.project_list_delete.listed_after = await evaluate(`[...document.querySelectorAll('[data-testid="project-row"]')].map((r) => r.dataset.project)`);
    step("project deleted from the list", { name: other });
  }
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
