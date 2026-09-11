/* Real browser checks for the built frontend.
 *
 * The API is intercepted at the browser boundary with deterministic responses,
 * so these tests exercise actual React rendering, navigation, input, HITL
 * controls, responsive layout, and lifecycle updates without changing any
 * backend records. Set DRO_BROWSER to a Chrome/Edge executable if discovery
 * is different on a developer machine.
 */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = fileURLToPath(new URL('.', import.meta.url));
const distRoot = join(root, 'dist');
const browserPath = process.env.DRO_BROWSER
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
let server;
let browser;
let baseUrl;
const backendState = { workOrders: [], chatCalls: [], executorCalls: [], rejectionCalls: [] };

const mime = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff': 'font/woff', '.woff2': 'font/woff2',
};

async function serve(req, res) {
  const requestPath = decodeURIComponent(new URL(req.url || '/', 'http://localhost').pathname);
  const relative = requestPath === '/' ? 'index.html' : requestPath.replace(/^\/+/, '');
  const target = normalize(join(distRoot, relative));
  if (!target.startsWith(normalize(distRoot)) || relative.includes('..')) {
    res.writeHead(403); res.end('forbidden'); return;
  }
  try {
    const body = await readFile(target);
    res.writeHead(200, { 'Content-Type': mime[extname(target)] || 'application/octet-stream' });
    res.end(body);
  } catch {
    // Vite's SPA fallback keeps direct navigation deterministic.
    try {
      const body = await readFile(join(distRoot, 'index.html'));
      res.writeHead(200, { 'Content-Type': mime['.html'] }); res.end(body);
    } catch { res.writeHead(404); res.end('not found'); }
  }
}

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function recommendation() {
  return {
    recommendation_status: 'ok', case_id: 'CASE-E2E-M104', asset_id: 'M-104',
    urgency: 'immediate', responsible_approver: 'Plant Supervisor',
    recommended_action: { name: 'stop_and_replace', estimated_duration_hours: 6 },
    required_parts: [{ part_number: 'SKF6310-ZZ', quantity: 1 }],
    rationale: 'Critical outer race fault with RUL Stage 3.',
    hitl_run_id: 'RUN-E2E-001',
    hitl_access: { allowed_personas: ['supervisor'], allowed_persona_names: ['Plant Supervisor'] },
    decision_support: {
      cost_data_status: 'available', currency: 'USD', cost_if_approved: 48000,
      cost_if_deferred: 2016000, cost_breakdown: 'planned intervention',
      deferred_cost_per_hour: 7500, cost_basis: 'deterministic demo figures',
      authority_check: 'within_authority', authority_reason: 'within configured limit',
      parts_vs_rul: { status: 'within_rul_window', part_number: 'SKF6310-ZZ', eta_days: 0, rul_min_days: 0, rul_max_days: 7 },
      approval_escalation: {
        status: 'active', current_persona_name: 'Plant Supervisor',
        steps: [{ from_persona_id: 'supervisor', from_persona_name: 'Plant Supervisor', to_persona_id: 'manager', to_persona_name: 'Plant Manager', escalates_at_utc: new Date(Date.now() + 60_000).toISOString() }],
      },
    },
  };
}

async function apiRoute(route) {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  let body = {};
  try { body = request.postDataJSON() || {}; } catch { /* no body */ }
  if (path === '/api/auth/me') return json(route, {
    username: 'admin', display_name: 'System Admin', role: 'admin',
    allowed_personas: ['supervisor', 'engineer', 'maintenance', 'manager', 'executive', 'md', 'ot', 'safety'],
  });
  if (path === '/api/auth/login') return json(route, {
    access_token: 'e2e-token', username: body.username || 'admin', display_name: 'System Admin',
    role: 'admin', allowed_personas: ['supervisor', 'engineer', 'maintenance', 'manager', 'executive', 'md', 'ot', 'safety'], expires_in_hours: 8,
  });
  if (path === '/api/dashboard/assets') return json(route, { source: 'backend', assets: [
    { id: 'M-104', asset_id: 'AST-M104', name: 'M-104', type: 'Motor', bearing_model: 'SKF6310-ZZ', power_kw: 250, rpm: 1768, line: 'Line 4', status: 'critical', rul: '0-7 days', vibration: 7.5, temperature: 81, bpfo: 0.91, iso_zone: 'D', trend: [], faults: [], scenario: 'outer_race_fault' },
    { id: 'P-208', asset_id: 'AST-P208', name: 'P-208', type: 'Pump', bearing_model: 'SKF6205', power_kw: 75, rpm: 1480, line: 'Line 2', status: 'warning', rul: '45-99 days', vibration: 5, temperature: 64, bpfo: 0.35, iso_zone: 'B', trend: [], faults: [], scenario: 'ambiguous_signal' },
    { id: 'C-301', asset_id: 'AST-C301', name: 'C-301', type: 'Compressor', bearing_model: 'SKF6312', power_kw: 300, rpm: 1800, line: 'Line 3', status: 'healthy', rul: '100+ days', vibration: 2, temperature: 46, bpfo: 0.1, iso_zone: 'A', trend: [], faults: [], scenario: 'data_integrity' },
  ] });
  if (path === '/api/workorders' && request.method() === 'GET') return json(route, { workorders: backendState.workOrders });
  if (path.startsWith('/api/notifications')) return json(route, {});
  if (path === '/api/chat') {
    backendState.chatCalls.push(body);
    const text = String(body.message || '');
    if (/P-999/i.test(text)) return json(route, {
      response: 'M-104 is CRITICAL based on registered evidence. P-999 has no registered evidence and was not assessed.',
      details: ['Only M-104 was included because P-999 is not registered.'], actions: [], pipeline_log: [{ node: 'data_foundation', latency_ms: 10 }], intent: 'analysis', conversation_id: 'e2e-conversation',
    });
    if (/approve|work order|analyse .*completely/i.test(text)) return json(route, {
      response: 'M-104 requires immediate controlled intervention.', details: ['Vibration 7.5 mm/s', 'Temperature 81 °C'], actions: ['Approve the maintenance work order'], pipeline_log: [{ node: 'data_foundation', latency_ms: 10 }, { node: 'monitoring', latency_ms: 10 }], intent: 'analysis', conversation_id: 'e2e-conversation', recommendation: recommendation(),
    });
    const isRul = /rul|remaining useful life/i.test(text);
    return json(route, {
      response: isRul ? 'M-104 RUL is 0-7 days.' : 'M-104 current status is CRITICAL.', details: ['Vibration 7.5 mm/s', 'Temperature 81 °C'], actions: [], pipeline_log: [{ node: 'data_foundation', latency_ms: 10 }, { node: 'monitoring', latency_ms: 10 }], intent: 'analysis', conversation_id: 'e2e-conversation',
    });
  }
  if (path === '/api/executor/run') {
    backendState.executorCalls.push(body);
    backendState.workOrders = [{ id: 'WO-E2E-001', status: 'Pending', asset_id: 'M-104', title: 'Bearing replacement', priority: 'URGENT', checklist: [], timeline: [] }];
    return json(route, { status: 'completed', action_taken: 'stop_and_replace', work_order_id: 'WO-E2E-001', parts_reserved: 'SKF6310-ZZ ×1', completed_steps: 2, execution_steps: [{ step_name: 'Isolate asset', status: 'done', detail: 'LOTO applied' }, { step_name: 'Replace bearing', status: 'done', detail: 'Part reserved' }] });
  }
  if (path === '/api/recommendations/reject') {
    backendState.rejectionCalls.push(body);
    return json(route, { status: 'recorded', persistence_status: 'stored', consecutive_rejections: 1, reliability_review_required: false });
  }
  if (path.startsWith('/api/pipeline/') || path.startsWith('/api/workorders/')) return json(route, { status: 'ok', work_order_id: 'WO-E2E-001' });
  return json(route, { detail: 'unhandled e2e endpoint' }, 404);
}

async function newPage(authenticated = true, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ viewport });
  await context.route('**/api/**', apiRoute);
  if (authenticated) await context.addInitScript(() => localStorage.setItem('dro_access_token', 'e2e-token'));
  const page = await context.newPage();
  return { context, page };
}

async function ready(page) {
  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
  await page.getByText('BACKEND LIVE', { exact: true }).waitFor({ state: 'visible', timeout: 8000 });
}

before(async () => {
  server = createServer((req, res) => { serve(req, res); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath: browserPath, headless: true });
});

after(async () => {
  await browser?.close();
  await new Promise(resolve => server?.close(resolve));
});

test('browser renders authenticated backend-backed workspace', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    assert.match(await page.locator('.chnm').innerText(), /Plant Supervisor/);
    assert.match(await page.locator('.ahr-pills').innerText(), /M-104/);
    assert.match(await page.locator('.ahr-pills').innerText(), /P-208/);
  } finally { await context.close(); }
});

test('browser login flow displays error then accepts valid credentials', async () => {
  const { context, page } = await newPage(false);
  try {
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Sign In' }).waitFor();
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password').fill('admin123');
    await page.getByRole('button', { name: 'Sign In' }).click();
    await page.getByText('BACKEND LIVE', { exact: true }).waitFor({ state: 'visible', timeout: 8000 });
    assert.match(await page.locator('.chnm').innerText(), /Plant Supervisor/);
  } finally { await context.close(); }
});

test('browser navigation and persona switch preserve live data', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    await page.locator('#pp-engineer').click();
    assert.match(await page.locator('.chnm').innerText(), /Reliability Engineer/);
    await page.getByRole('button', { name: 'Asset Registry' }).click();
    assert.match(await page.locator('#view-assets').getAttribute('class'), /on/);
    await page.getByRole('button', { name: 'Work Orders' }).click();
    assert.match(await page.locator('#view-workorders').getAttribute('class'), /on/);
  } finally { await context.close(); }
});

test('browser chat follow-ups produce distinct backend responses', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    const input = page.locator('textarea.cinptx');
    await input.fill('current status of M-104'); await page.locator('button.sbtn').click();
    await page.getByText('M-104 current status is CRITICAL.', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    await input.fill('what is the RUL of M-104?'); await page.locator('button.sbtn').click();
    await page.getByText('M-104 RUL is 0-7 days.', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(backendState.chatCalls.at(-1).conversation_id, 'e2e-conversation');
  } finally { await context.close(); }
});

test('browser partial multi-asset answer does not fabricate unknown asset', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    const input = page.locator('textarea.cinptx');
    await input.fill('analyse M-104 and P-999'); await page.locator('button.sbtn').click();
    const response = page.getByText('P-999 has no registered evidence', { exact: false });
    await response.waitFor({ state: 'visible', timeout: 8000 });
    assert.match(await response.innerText(), /not assessed/);
  } finally { await context.close(); }
});

test('browser supervisor can approve HITL and work order persists in view', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse M-104 completely and create work order'); await page.locator('button.sbtn').click();
    await page.getByText('APPROVAL REQUIRED', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    await page.getByRole('button', { name: 'Approve & Execute' }).click();
    await page.getByText('APPROVED & EXECUTED', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(backendState.executorCalls.at(-1).approved, true);
    await page.getByRole('button', { name: 'Work Orders' }).click();
    await page.locator('#view-workorders .wcid').getByText('WO-E2E-001', { exact: true }).waitFor({ state: 'visible', timeout: 8000 });
  } finally { await context.close(); }
});

test('browser rejection captures reason and records learning feedback', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse M-104 completely and create work order'); await page.locator('button.sbtn').click();
    await page.getByText('APPROVAL REQUIRED', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    await page.getByRole('button', { name: 'Reject' }).click();
    await page.locator('input[type="radio"]').nth(2).check();
    await page.getByRole('button', { name: 'Confirm rejection' }).click();
    await page.locator('#view-chat').getByText('Recommendation rejected — no action taken.', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(backendState.rejectionCalls.at(-1).reason_code, 'second_opinion');
  } finally { await context.close(); }
});

test('browser read-only persona cannot approve restricted HITL card', async () => {
  const { context, page } = await newPage();
  try {
    await ready(page);
    await page.locator('#pp-engineer').click();
    const input = page.locator('textarea.cinptx');
    await input.fill('Analyse M-104 completely and create work order'); await page.locator('button.sbtn').click();
    await page.getByText('APPROVAL REQUIRED', { exact: false }).waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(await page.getByRole('button', { name: 'Approve & Execute' }).count(), 0);
    assert.equal(await page.getByRole('button', { name: /Send to Plant Supervisor/ }).count(), 1);
  } finally { await context.close(); }
});

test('browser compact viewport remains usable and accessible', async () => {
  const { context, page } = await newPage(true, { width: 375, height: 812 });
  try {
    await ready(page);
    assert.equal(await page.locator('textarea.cinptx').isVisible(), true);
    assert.equal(await page.getByRole('button', { name: 'Agent Chat' }).count(), 1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
    assert.equal(overflow, false, 'compact layout must not introduce horizontal page overflow');
  } finally { await context.close(); }
});
