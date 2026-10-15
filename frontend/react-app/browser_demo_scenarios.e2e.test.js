/* Aggressive browser acceptance pack for the five manager demo scenarios.
 *
 * This suite talks to a real FastAPI process and the built React bundle.  It
 * deliberately uses the same clicks, persona switches, refreshes and tabs as
 * a demo user; only assertions use the API token to verify durable records.
 */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const enabled = process.env.DRO_DEMO_BROWSER === '1';
const backendBase = (process.env.DRO_DEMO_BROWSER_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const browserPath = process.env.DRO_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const distRoot = fileURLToPath(new URL('./dist/', import.meta.url));
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };
let browser;
let frontendServer;
let frontendBase;
let token = '';

async function serveFrontend(req, res) {
  const url = new URL(req.url || '/', 'http://localhost');
  if (url.pathname.startsWith('/api/')) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? Buffer.concat(chunks) : undefined;
    const headers = {};
    if (req.headers.authorization) headers.authorization = req.headers.authorization;
    if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
    const response = await fetch(`${backendBase}${url.pathname}${url.search}`, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : body,
    });
    res.writeHead(response.status, { 'content-type': response.headers.get('content-type') || 'application/json' });
    res.end(Buffer.from(await response.arrayBuffer()));
    return;
  }
  const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/+/, '');
  const target = normalize(join(distRoot, relative));
  if (!target.startsWith(normalize(distRoot)) || relative.includes('..')) {
    res.writeHead(403); res.end('forbidden'); return;
  }
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

async function api(path, options = {}) {
  const response = await fetch(`${backendBase}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  assert.equal(response.ok, true, `${options.method || 'GET'} ${path} returned HTTP ${response.status}`);
  return response.json();
}

async function newPage() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript({ content: `localStorage.setItem('dro_access_token', ${JSON.stringify(token)});` });
  const page = await context.newPage();
  await page.goto(`${frontendBase}/`, { waitUntil: 'domcontentloaded' });
  await page.getByText('BACKEND LIVE', { exact: true }).waitFor({ state: 'visible', timeout: 45000 });
  return { context, page };
}

async function nav(page, label) {
  await page.getByRole('button', { name: label, exact: true }).click();
  const ids = { 'Agent Chat': '#view-chat', 'Agent Graph': '#view-agents', 'Asset Registry': '#view-assets', 'Work Orders': '#view-workorders' };
  await page.locator(`${ids[label]}.on`).waitFor({ state: 'visible', timeout: 15000 });
}

async function persona(page, id) {
  await page.locator(`#pp-${id}`).click();
  await page.locator(`#pp-${id}.on`).waitFor({ state: 'visible', timeout: 10000 });
}

async function ask(page, question, timeout = 60000) {
  await nav(page, 'Agent Chat');
  const replies = page.locator('.msgs .bbl.a');
  const before = await replies.count();
  const input = page.locator('textarea.cinptx');
  await input.fill(question);
  await page.locator('button.sbtn').click();
  await page.waitForFunction(
    expected => document.querySelectorAll('.msgs .bbl.a').length > expected,
    before,
    { timeout },
  );
  const reply = replies.nth(before);
  await reply.waitFor({ state: 'visible', timeout });
  return (await reply.innerText()).trim();
}

function mustContain(text, pattern, label) {
  assert.match(text, pattern, `${label}: ${text.slice(0, 500)}`);
}

before(async () => {
  if (!enabled) return;
  browser = await chromium.launch({ executablePath: browserPath, headless: true });
  frontendServer = createServer((req, res) => {
    serveFrontend(req, res).catch(error => { res.writeHead(502); res.end(String(error)); });
  });
  await new Promise(resolve => frontendServer.listen(0, '127.0.0.1', resolve));
  frontendBase = `http://127.0.0.1:${frontendServer.address().port}`;
  const response = await fetch(`${backendBase}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  });
  assert.equal(response.ok, true, `login returned HTTP ${response.status}`);
  token = (await response.json()).access_token;
  assert.ok(token);
});
after(async () => {
  await browser?.close();
  if (frontendServer) await new Promise(resolve => frontendServer.close(resolve));
});

test('all frontend tabs render backend data and preserve persona context', { skip: !enabled, concurrency: false, timeout: 120000 }, async () => {
  const { context, page } = await newPage();
  try {
    await nav(page, 'Agent Graph');
    await page.locator('#view-agents').getByText('Agent Pipeline', { exact: true }).waitFor({ state: 'visible' });
    await page.locator('.agsel').first().selectOption('P-208');
    await page.getByText(/LIVE|FALLBACK/).first().waitFor({ state: 'visible', timeout: 45000 });

    await nav(page, 'Asset Registry');
    await page.getByPlaceholder(/Search assets/i).fill('P-208');
    const asset = page.locator('.ait').filter({ hasText: 'P-208' });
    assert.equal(await asset.count(), 1);
    await asset.click();
    mustContain(await page.locator('#view-assets').innerText(), /P-208[\s\S]*(WARNING|warning)/i, 'registry status');

    await nav(page, 'Work Orders');
    await page.getByText('Work Orders', { exact: true }).first().waitFor({ state: 'visible' });
    await nav(page, 'Agent Chat');
    await persona(page, 'engineer');
    mustContain(await page.locator('.chnm').innerText(), /Reliability Engineer/i, 'persona header');
    await persona(page, 'supervisor');
  } finally { await context.close(); }
});
test('Demo 1 critical fault closes the loop and remains healthy after refresh', { skip: !enabled, concurrency: false, timeout: 240000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'supervisor');
    const diagnosis = await ask(page, 'Diagnose the fault on M-104');
    mustContain(diagnosis, /M-104/i, 'diagnosis asset');
    mustContain(diagnosis, /outer race|fault|stage 3|critical/i, 'diagnosis evidence');

    const defer = await ask(page, 'Can this motor wait until the weekend shutdown instead?');
    mustContain(defer, /0.?7 days|critical|defer|weekend|risk/i, 'run/defer answer');
    assert.notEqual(defer, diagnosis, 'different questions must not repeat the exact same response');

    await ask(page, 'Analyse M-104 completely and tell me what we need to do');
    const approval = page.getByText('APPROVAL REQUIRED', { exact: false }).last();
    await approval.waitFor({ state: 'visible', timeout: 45000 });
    await page.getByRole('button', { name: /Approve & Execute/i }).last().click();
    await page.getByText(/APPROVED & EXECUTED/i).last().waitFor({ state: 'visible', timeout: 45000 });

    await nav(page, 'Work Orders');
    const card = page.locator('.wcard').filter({ hasText: /AST_MTR_001|M-104/ }).first();
    await card.waitFor({ state: 'visible', timeout: 20000 });
    await card.click();
    await page.getByRole('button', { name: /Approve WO/i }).click();
    await page.getByText('SCHEDULED', { exact: true }).waitFor({ state: 'visible' });
    await page.getByRole('button', { name: /Mark In Progress/i }).click();
    await page.getByText('IN PROGRESS', { exact: true }).waitFor({ state: 'visible' });
    await page.getByRole('button', { name: /Mark Complete/i }).click();
    await page.getByText('CLOSED', { exact: true }).waitFor({ state: 'visible' });

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByText('BACKEND LIVE', { exact: true }).waitFor({ state: 'visible', timeout: 45000 });
    const current = await ask(page, 'What is the current status of M-104?');
    mustContain(current, /healthy after completed maintenance|completed maintenance|current status is healthy/i, 'post-repair current status');
    const oldApproval = page.locator('.bbl.a').filter({ hasText: 'APPROVAL REQUIRED' }).filter({ hasText: 'AST_MTR_001' });
    assert.equal(await oldApproval.getByRole('button', { name: /Approve & Execute/i }).count(), 0,
      'a resolved historical card may remain visible but must not be actionable');

    const historical = await ask(page, 'What was the RUL of M-104 before the work order?');
    mustContain(historical, /0.?7 days|pre-repair|historical/i, 'historical RUL');
    const counterfactual = await ask(page, 'What would have happened if I rejected the work order?');
    mustContain(counterfactual, /reject|would|continued|risk|critical/i, 'counterfactual decision');
    assert.notEqual(historical, counterfactual);
  } finally { await context.close(); }
});
test('Demo 2 P-208 ambiguity forwards an actionable card and returns the decision', { skip: !enabled, concurrency: false, timeout: 240000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'supervisor');
    const anomalous = await ask(page, 'Is P-208 anomalous?');
    mustContain(anomalous, /P-208|anomal/i, 'P-208 anomaly');
    const readings = await ask(page, "What are the exact vibration readings on P-208 and what's driving the anomaly score?");
    mustContain(readings, /vibration|mm\/s|anomaly|score/i, 'P-208 readings');
    const watch = await ask(page, 'What should we watch for on P-208 over the next week?');
    mustContain(watch, /watch|monitor|vibration|temperature|inspection/i, 'P-208 watch plan');
    assert.notEqual(readings, watch, 'reading and watch-plan responses must differ');

    await ask(page, 'Analyse P-208 completely');
    const forward = page.getByRole('button', { name: /Send to Reliability Engineer/i }).last();
    await forward.waitFor({ state: 'visible', timeout: 45000 });
    await forward.click();
    await persona(page, 'engineer');
    await page.getByText('FAULT CLASSIFICATION REVIEW', { exact: false }).last().waitFor({ state: 'visible', timeout: 30000 });
    const confirm = page.getByRole('button', { name: /^Confirm:/i }).last();
    await confirm.waitFor({ state: 'visible' });
    await confirm.click();
    await page.getByText(/HITL RESOLVED/i).last().waitFor({ state: 'visible', timeout: 45000 });

    await persona(page, 'supervisor');
    await page.locator('.bbl.a').filter({ hasText: 'DIAGNOSIS RESOLVED' }).last().waitFor({ state: 'visible', timeout: 30000 });
    const followup = await ask(page, 'What is the action taken by Reliability Engineer on P-208?');
    mustContain(followup, /confirm|classification|reliability|diagnos/i, 'forwarded decision follow-up');
  } finally { await context.close(); }
});

test('Demo 3 C-301 data remediation is actionable and persists after refresh', { skip: !enabled, concurrency: false, timeout: 180000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'supervisor');
    const quality = await ask(page, 'Why is C-301 data quality suspect?');
    mustContain(quality, /C-301|quality|missing|dropout|signal/i, 'quality explanation');
    await ask(page, 'Analyse C-301 completely');
    await page.getByText('SIGNAL QUALITY ISSUE', { exact: false }).last().waitFor({ state: 'visible', timeout: 45000 });
    const impute = page.getByRole('button', { name: /Impute from baseline/i }).last();
    assert.equal(await impute.isEnabled(), true, 'C-301 baseline imputation must be available');
    await impute.click();
    await page.getByText(/Imputed from baseline/i).last().waitFor({ state: 'visible', timeout: 45000 });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByText('BACKEND LIVE', { exact: true }).waitFor({ state: 'visible', timeout: 45000 });
    const followup = await ask(page, 'How was the C-301 signal quality issue handled?');
    mustContain(followup, /imput|baseline|quality/i, 'durable remediation follow-up');
  } finally { await context.close(); }
});

test('Demo 4 executive and fleet questions retain every known asset and mark unknown assets', { skip: !enabled, concurrency: false, timeout: 240000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'executive');
    const brief = await ask(page, 'Give me an executive decision brief for M-104');
    mustContain(brief, /M-104/i, 'executive brief');
    const plan = await ask(page, 'Give me the complete action plan for this week across M-104, P-207, P-208 and C-301 — what do I need to do and by when?', 90000);
    for (const asset of ['M-104', 'P-207', 'P-208', 'C-301']) mustContain(plan, new RegExp(asset), `fleet plan ${asset}`);

    const mixed = await ask(page, 'Compare M-104, P-207 and ZX-999 and give the available action plan', 90000);
    mustContain(mixed, /M-104/i, 'mixed known M-104');
    mustContain(mixed, /P-207/i, 'mixed known P-207');
    mustContain(mixed, /ZX-999/i, 'mixed unknown ID');
    mustContain(mixed, /unavailable|unknown|not found|no data/i, 'unknown asset qualification');

    const cost = await ask(page, 'What is the cost trade-off for M-104 — planned replacement now versus waiting?');
    mustContain(cost, /cost|USD|\$|planned|defer/i, 'cost tradeoff');
    const rul = await ask(page, 'What is the RUL of M-104?');
    mustContain(rul, /RUL|0.?7 days|historical|pre-repair/i, 'RUL answer');
    assert.notEqual(cost, rul, 'cost and RUL questions must not repeat the same response');
  } finally { await context.close(); }
});

test('Demo 5 learning answer is user-facing and distinguishes observation from confirmed truth', { skip: !enabled, concurrency: false, timeout: 120000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'supervisor');
    const learned = await ask(page, 'What did you learn from M-104?');
    mustContain(learned, /learning|observ|completed|outcome|maintenance/i, 'learning response');
    assert.doesNotMatch(learned, /Agent 8 (found|memory|feedback)/i, 'response must not expose internal agent implementation');
    const repeated = await ask(page, 'Summarise the reusable operational lesson from M-104');
    mustContain(repeated, /M-104|learning|lesson|maintenance|evidence/i, 'learning paraphrase');
  } finally { await context.close(); }
});

test('rejected Work Order remains rejected/active-risk and records a useful learning', { skip: !enabled, concurrency: false, timeout: 240000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'supervisor');
    await ask(page, 'Analyse G-120 completely');
    await page.getByRole('button', { name: /Approve & Execute/i }).last().click();
    await page.getByText(/APPROVED & EXECUTED/i).last().waitFor({ state: 'visible', timeout: 45000 });
    await nav(page, 'Work Orders');
    const card = page.locator('.wcard').filter({ hasText: /AST_SYN_GBX_120|G-120/ }).first();
    await card.waitFor({ state: 'visible', timeout: 20000 });
    await card.click();
    await page.getByRole('button', { name: /Reject WO/i }).click();
    await page.locator('.wo-reject-form select').selectOption('second_opinion');
    await page.locator('.wo-reject-form textarea').fill('Verify the competing vibration evidence before resubmission.');
    await page.getByRole('button', { name: /Confirm Reject/i }).click();
    await page.getByText('REJECTED', { exact: true }).waitFor({ state: 'visible', timeout: 20000 });
    await page.getByText(/valid closure|Rejection recorded/i).waitFor({ state: 'visible', timeout: 20000 });

    const status = await ask(page, 'What is the current status of G-120 after I rejected the work order?');
    mustContain(status, /rejected|no maintenance|remains|risk|critical/i, 'rejected lifecycle');
    assert.doesNotMatch(status, /current status is healthy after completed maintenance/i);
    const learning = await ask(page, 'What did you learn from G-120?');
    mustContain(learning, /verify|evidence|second opinion|validation|learning/i, 'rejection lesson');
  } finally { await context.close(); }
});

test('full escalation reaches actionable VP card, disables old owners, and never loops', { skip: !enabled, concurrency: false, timeout: 180000 }, async () => {
  const { context, page } = await newPage();
  try {
    await persona(page, 'supervisor');
    await ask(page, 'Analyse M-121 completely');
    const supervisorCard = page.locator('.bbl.a').filter({ hasText: 'AST_SYN_MTR_121' }).filter({ hasText: 'APPROVAL REQUIRED' }).last();
    await supervisorCard.waitFor({ state: 'visible', timeout: 45000 });
    await page.waitForTimeout(34000);
    assert.equal(await supervisorCard.getByRole('button', { name: /Approve & Execute/i }).count(), 0, 'expired supervisor card must not remain actionable');

    await persona(page, 'manager');
    const managerCard = page.locator('.bbl.a').filter({ hasText: 'AST_SYN_MTR_121' }).filter({ hasText: 'APPROVAL REQUIRED' }).last();
    await managerCard.waitFor({ state: 'visible', timeout: 20000 });
    await page.waitForTimeout(34000);
    assert.equal(await managerCard.getByRole('button', { name: /Approve & Execute/i }).count(), 0, 'expired manager card must not remain actionable');

    await persona(page, 'executive');
    const executiveCard = page.locator('.bbl.a').filter({ hasText: 'AST_SYN_MTR_121' }).filter({ hasText: 'APPROVAL REQUIRED' }).last();
    await executiveCard.waitFor({ state: 'visible', timeout: 20000 });
    const approve = executiveCard.getByRole('button', { name: /Approve & Execute/i });
    await approve.waitFor({ state: 'visible' });
    assert.equal(await executiveCard.getByLabel('approval escalation countdown').count(), 0, 'final authority must not have another timer');
    assert.equal(await executiveCard.getByRole('button', { name: /Send to Plant Supervisor/i }).count(), 0, 'terminal card must not loop to supervisor');
    await approve.click();
    await page.getByText(/APPROVED & EXECUTED/i).last().waitFor({ state: 'visible', timeout: 45000 });

    const pending = await api('/api/pipeline/hitl/pending?persona=executive&gate=execution');
    assert.equal(pending.items.some(item => String(item.asset_id || item.hitl_payload?.asset_id).includes('M-121')), false);
  } finally { await context.close(); }
});
// End of browser demo scenario coverage.
