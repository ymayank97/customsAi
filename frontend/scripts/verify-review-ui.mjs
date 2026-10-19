import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const frontend = join(root, "frontend");
const artifacts = join(root, "var", "ui-review-check");
const chrome = process.env.CHROME_PATH || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/chromium", "/usr/bin/google-chrome",
].find(existsSync);
assert(chrome, "Set CHROME_PATH to an installed Chromium browser.");
const profile = mkdtempSync(join(tmpdir(), "customsai-review-ui-"));
mkdirSync(artifacts, { recursive: true });
const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "-p", "3100", "-H", "127.0.0.1"], { cwd: frontend, windowsHide: true, stdio: "ignore" });
let browser, socket;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let sequence = 0;
const pending = new Map();
const errors = [];
const user = { id: 1, email: "officer@example.test", name: "Customs Officer", picture: "", account_tier: "premium", can_label_rows: true, is_admin: true };
const source = { row_idx: 0, item_description: "Portable computer with keyboard and display, invoice specifications available", declared_cth: "84713090", declared_bcd: 10,
  final: { cth_final: "84713010", bcd_final: 10, tier_used: "stage1_fallback", confidence: 0.45, reasoning: "Check product specifications before classifying.", candidates: [], manual_queue: true },
  fraud: { is_fraud: false, flag_type: "CTH Mismatch", revenue_at_risk: null, detail: "Declared and suggested codes differ." }, review_flags: ["manual_review"] };
let review = null;
const job = { id: "ui-audit", filename: "Sample Bill of Entry.xlsx", status: "done", rows_total: 1, rows_processed: 1, completed_at: Date.now() / 1000, started_at: Date.now() / 1000 - 60 };
const googleStub = 'window.google={accounts:{id:{initialize:function(c){window.googleTestCallback=c.callback},renderButton:function(el){const b=document.createElement("button");b.textContent="Continue with Google";b.onclick=function(){window.googleTestCallback({credential:"test-credential"})};el.appendChild(b)},prompt:function(){},cancel:function(){},disableAutoSelect:function(){}}}};';

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error("CDP timeout: " + method)); }, 15000);
    pending.set(id, { resolve: result => { clearTimeout(timeout); resolve(result); }, reject: error => { clearTimeout(timeout); reject(error); } });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function waitFor(expression, message) {
  for (let i = 0; i < 100; i++) { if (await evaluate("Boolean(" + expression + ")")) return; await delay(100); }
  throw new Error("UI did not reach: " + message);
}
async function navigate(path) {
  await send("Page.navigate", { url: "http://127.0.0.1:3100" + path });
}
async function screenshot(name) {
  const result = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(artifacts, name + ".png"), Buffer.from(result.data, "base64"));
}
async function intercept(event) {
  const url = new URL(event.request.url);
  let body = {}, contentType = "application/json";
  if (url.hostname === "accounts.google.com") { body = googleStub; contentType = "application/javascript"; }
  else if (event.request.method === "OPTIONS") body = {};
  else if (url.pathname === "/api/auth/config") body = { auth_required: true, google_client_id: "ui-test-client" };
  else if (url.pathname === "/api/auth/google") body = { access_token: "test-session", user };
  else if (url.pathname === "/api/auth/me") body = { authenticated: true, user };
  else if (url.pathname === "/api/health") body = { status: "ok", enriched_master: true };
  else if (url.pathname === "/api/uploads") body = [job];
  else if (url.pathname.endsWith("/results")) body = { total: 1, rows: [{ ...source, review }] };
  else if (url.pathname === "/api/uploads/ui-audit") body = job;
  else if (url.pathname.startsWith("/api/cth/")) body = { description: "Portable automatic data processing machines" };
  else if (url.pathname === "/api/reviews" && event.request.method === "POST") {
    const payload = JSON.parse(event.request.postData);
    assert.equal(payload.corrected_cth, "84713090");
    assert(!("item_description" in payload), "Browser must not supply source description");
    review = { ...payload, id: 1, user_id: 1, item_description: source.item_description, predicted_cth: source.final.cth_final, declared_cth: source.declared_cth,
      reviewer_name: user.name, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), quality: "ready" };
    body = { ok: true, review };
  } else if (url.pathname === "/api/reviews") body = { items: review ? [review] : [], total: review ? 1 : 0, scope: "all_reviewers", summary: { reviewed: review ? 1 : 0, training_examples: review ? 1 : 0, needs_information: 0, conflicts: 0 } };
  else throw new Error("Unexpected mocked request: " + url.pathname);
  await send("Fetch.fulfillRequest", { requestId: event.requestId, responseCode: 200,
    responseHeaders: [{ name: "Content-Type", value: contentType }, { name: "Access-Control-Allow-Origin", value: "*" }, { name: "Access-Control-Allow-Headers", value: "Authorization, Content-Type" }, { name: "Access-Control-Allow-Methods", value: "GET, POST, OPTIONS" }],
    body: Buffer.from(typeof body === "string" ? body : JSON.stringify(body)).toString("base64") });
}

try {
  for (let i = 0; i < 100; i++) { try { if ((await fetch("http://127.0.0.1:3100/login")).ok) break; } catch {} await delay(100); }
  browser = spawn(chrome, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-background-networking", "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank"], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
  browser.stderr.on("data", data => { if (process.env.UI_DEBUG) process.stderr.write(data); });
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !existsSync(portFile); i++) await delay(100);
  assert(existsSync(portFile), "Chromium did not start");
  const port = readFileSync(portFile, "utf8").split(/\r?\n/)[0];
  const tabs = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
  socket = new WebSocket(tabs.find(tab => tab.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = message => {
    const event = JSON.parse(message.data);
    if (process.env.UI_DEBUG) console.log(event.id || event.method);
    if (event.id) { const promise = pending.get(event.id); pending.delete(event.id); if (promise) event.error ? promise.reject(new Error(event.error.message)) : promise.resolve(event.result); }
    else if (event.method === "Fetch.requestPaused") void intercept(event.params).catch(error => errors.push(error.message));
    else if (event.method === "Runtime.exceptionThrown") errors.push(event.params.exceptionDetails.exception?.description || event.params.exceptionDetails.text);
  };
  await send("Browser.getVersion");
  await send("Page.enable"); await send("Runtime.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "http://127.0.0.1:8000/*" }, { urlPattern: "https://accounts.google.com/*" }] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await navigate("/login");
  await waitFor('document.body.innerText.includes("Continue with Google")', "login button");
  assert(await evaluate('document.body.innerText.includes("Standard access") && document.body.innerText.includes("Premium reviewer")'));
  await screenshot("login");
  await evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Continue with Google").click()');
  await waitFor('window.location.pathname === "/" && document.body.innerText.includes("Welcome")', "signed-in home");
  await navigate("/results/ui-audit");
  await waitFor(`document.querySelector('button[aria-label="Review item 1"]')`, "review action");
  assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), "Closed drawers should not make the results page overflow");
  await screenshot("results");
  await evaluate(`document.querySelector('button[aria-label="Review item 1"]').click()`);
  await waitFor('document.querySelector("[role=dialog]")', "review dialog");
  await evaluate('document.querySelector("input[value=corrected]").click()');
  await evaluate(`(() => { const inputs = document.querySelectorAll('[role=dialog] input:not([type=radio])'); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(inputs[0], '84713090'); inputs[0].dispatchEvent(new Event('input',{bubbles:true})); setter.call(inputs[1], 'Invoice and tariff note'); inputs[1].dispatchEvent(new Event('input',{bubbles:true})); const note=document.querySelector('[role=dialog] textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(note,'Verified product specifications and intended use.'); note.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await waitFor('document.querySelector("[role=dialog]").innerText.includes("Portable automatic")', "tariff lookup");
  await screenshot("review-dialog");
  await evaluate('document.querySelector("[role=dialog] button[type=submit]").click()');
  await waitFor('document.body.innerText.includes("Your review is saved")', "save success");
  await evaluate('Array.from(document.querySelectorAll("[role=dialog] button")).find(b=>b.textContent==="Done").click()');
  await send("Page.reload");
  await waitFor('document.body.innerText.includes("Edit review")', "persistent review after reload");
  assert(await evaluate('document.body.innerText.includes("84713010") && document.body.innerText.includes("84713090")'));
  await navigate("/reviewed-items");
  await waitFor('document.querySelector("table") && document.body.innerText.includes("Ready")', "review library");
  await screenshot("reviewed-items");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await screenshot("reviewed-items-mobile");
  assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), "Mobile page should not overflow horizontally");
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log("PASS: login, premium distinction, correction, tariff lookup, save, reload persistence, review library and mobile layout. Mocked API/Google fixtures; screenshots: " + artifacts);
} finally {
  socket?.close(); browser?.kill(); server.kill();
  await delay(500);
  assert.equal(dirname(resolve(profile)), resolve(tmpdir()));
  assert(basename(profile).startsWith("customsai-review-ui-"));
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch {}
}
