/* Browser checks against a real FastAPI instance (no API mocking).
 * Enable explicitly with DRO_LIVE_BROWSER=1 and optionally set
 * DRO_LIVE_BROWSER_URL. The test logs in with the local demo account, then
 * verifies backend-backed rendering, RAG citations, and a full asset query.
 */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const enabled = process.env.DRO_LIVE_BROWSER === '1';
const backendBase = (process.env.DRO_LIVE_BROWSER_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const browserPath = process.env.DRO_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
let browser;
let frontendServer;
let frontendBase;
let token = '';

// The FastAPI service intentionally does not serve the React bundle.  For a
// true browser test, host the already-built bundle and proxy only /api calls
// to the live backend, exactly as the production reverse proxy does.
const distRoot = fileURLToPath(new URL('./dist/', import.meta.url));
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff': 'font/woff', '.woff2': 'font/woff2' };

async function serveFrontend(req, res) {
  const requestUrl = new URL(req.url || '/', 'http://localhost');
  if (requestUrl.pathname.startsWith('/api/')) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? Buffer.concat(chunks) : undefined;
    const headers = {};
    if (req.headers.authorization) headers.authorization = req.headers.authorization;
    if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
    const response = await fetch(`${backendBase}${requestUrl.pathname}${requestUrl.search}`, { method: req.method, headers, body: req.method === 'GET' || req.method === 'HEAD' ? undefined : body });
    const output = Buffer.from(await response.arrayBuffer());
    res.writeHead(response.status, { 'content-type': response.headers.get('content-type') || 'application/json' });
    res.end(output);
    return;
  }
  const relative = requestUrl.pathname === '/' ? 'index.html' : requestUrl.pathname.replace(/^\/+/, '');
  const target = normalize(join(distRoot, relative));
  if (!target.startsWith(normalize(distRoot)) || relative.includes('..')) { res.writeHead(403); res.end('forbidden'); return; }
  try {
    const body = await readFile(target);
    res.writeHead(200, { 'content-type': mime[extname(target)] || 'application/octet-stream' });
    res.end(body);
  } catch {
    const body = await readFile(join(distRoot, 'index.html'));
    res.writeHead(200, { 'content-type': mime['.html'] });
    res.end(body);
  }
}

async function login() {
  const response = await fetch(`${backendBase}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  });
  assert.equal(response.ok, true, `login returned HTTP ${response.status}`);
  token = (await response.json()).access_token;
  assert.ok(token, 'login must return a token');
}

async function pageForLiveApp() {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  // Use the object form so Chromium receives the token as a literal init
  // script rather than relying on argument serialization across versions.
  await context.addInitScript({ content: `localStorage.setItem('dro_access_token', ${JSON.stringify(token)});` });
  const page = await context.newPage();
  await page.goto(`${frontendBase}/`, { waitUntil: 'domcontentloaded' });
  // Dashboard projection evaluates the configured fleet (30 assets) and can
  // legitimately take longer than a simple static-page readiness check.
  await page.getByText('BACKEND LIVE', { exact: true }).waitFor({ state: 'visible', timeout: 30000 });
  return { context, page };
}

before(async () => {
  if (!enabled) return;
  browser = await chromium.launch({ executablePath: browserPath, headless: true });
  frontendServer = createServer((req, res) => { serveFrontend(req, res).catch(error => { res.writeHead(502); res.end(String(error)); }); });
  await new Promise(resolve => frontendServer.listen(0, '127.0.0.1', resolve));
  frontendBase = `http://127.0.0.1:${frontendServer.address().port}`;
  await login();
});

after(async () => { await browser?.close(); await new Promise(resolve => frontendServer?.close(resolve)); });

test('live browser renders backend fleet data', { skip: !enabled }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    assert.match(await page.locator('.ahr-pills').innerText(), /P-208/);
    assert.ok((await page.locator('.ahr-pills button').count()) > 0, 'live fleet ribbon must contain assets');
    assert.equal(await page.getByText('Backend offline', { exact: false }).count(), 0);
  } finally { await context.close(); }
});

test('live browser displays RAG citation from approved SOP corpus', { skip: !enabled }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    const input = page.locator('textarea.cinptx');
    await input.fill('What does ISO 10816-3 require?');
    await page.locator('button.sbtn').click();
    await page.getByText('[1]', { exact: false }).waitFor({ state: 'visible', timeout: 30000 });
    assert.match(await page.locator('.msgs').innerText(), /ISO 10816-3|\[1\]/);
  } finally { await context.close(); }
});

test('live browser routes a complete M-104 analysis through the backend', { skip: !enabled }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse M-104 completely');
    await page.locator('button.sbtn').click();
    // The current ChatView renders the pipeline trace as route badges (and
    // keeps the response text in the message bubble); it does not use the
    // older standalone “REAL PIPELINE” heading on this path.
    await page.locator('.msgs .bbl.a').filter({ hasText: 'M-104' }).last().waitFor({ state: 'visible', timeout: 30000 });
    const text = await page.locator('.msgs').innerText();
    assert.match(text, /M-104/);
    assert.match(text, /Current status|CRITICAL|healthy/);
  } finally { await context.close(); }
});

test('live browser restores a durable executor card after refresh', { skip: !enabled }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    const input = page.locator('textarea.cinptx');
    // Keep this independent from the escalation test and from a previous
    // local run that may already have transferred P-207 to Plant Manager.
    await input.fill('Analyse C-315 completely');
    await page.locator('button.sbtn').click();
    await page.getByText('APPROVAL REQUIRED', { exact: false }).first()
      .waitFor({ state: 'visible', timeout: 30000 });
    assert.match(await page.locator('.msgs').innerText(), /C-315|AST_SYN_CON_315/);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByText('APPROVAL REQUIRED', { exact: false }).first()
      .waitFor({ state: 'visible', timeout: 30000 });
    assert.match(await page.locator('.msgs').innerText(), /C-315|AST_SYN_CON_315/);
  } finally { await context.close(); }
});

test('live browser does not create a new approval card for healthy M-104', { skip: !enabled }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse M-104 completely');
    await page.locator('button.sbtn').click();
    await page.locator('.msgs .bbl.a').filter({ hasText: 'M-104' }).last()
      .waitFor({ state: 'visible', timeout: 30000 });
    const m104Cards = page.locator('.bbl.a').filter({ hasText: 'Asset: AST_MTR_001' });
    assert.equal(await m104Cards.count(), 0, 'healthy completed asset must not show a new executor card');
  } finally { await context.close(); }
});

test('live browser escalates an unanswered approval to the next persona', { skip: !enabled, timeout: 75000 }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse P-207 completely');
    await page.locator('button.sbtn').click();
    await page.getByText('APPROVAL REQUIRED', { exact: false }).first()
      .waitFor({ state: 'visible', timeout: 30000 });
    // Demo configuration uses a 30-second approval timeout.  Wait past the
    // deadline, then verify the manager inbox contains the actual card.
    await page.waitForTimeout(34000);
    await page.locator('#pp-manager').click();
    await page.getByText('APPROVAL REQUIRED', { exact: false }).first()
      .waitFor({ state: 'visible', timeout: 10000 });
    const body = await page.locator('.msgs').innerText();
    assert.match(body, /P-207|AST_PMP_001/);
    assert.match(body, /Plant Manager|Final approval authority|Approve & Execute/);
  } finally { await context.close(); }
});

test('live browser forwards a read-only diagnosis card to Reliability Engineer', { skip: !enabled }, async () => {
  const { context, page } = await pageForLiveApp();
  try {
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse P-208 completely');
    await page.locator('button.sbtn').click();
    const forward = page.getByRole('button', { name: /Send to Reliability Engineer/i }).last();
    await forward.waitFor({ state: 'visible', timeout: 30000 });
    await forward.click();
    await page.locator('#pp-engineer').click();
    await page.getByText('FAULT CLASSIFICATION REVIEW', { exact: false }).last()
      .waitFor({ state: 'visible', timeout: 15000 });
    const body = await page.locator('.msgs').innerText();
    assert.match(body, /Confirm:|Override|P-208|AST_PMP_002/);
  } finally { await context.close(); }
});
