/* Optional live-service smoke tests.
 * Run with DRO_E2E_BASE_URL=http://localhost:8000 (and optionally
 * DRO_E2E_TOKEN=<JWT>). They are skipped when no service URL is supplied so
 * normal offline frontend tests remain deterministic.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const base = (process.env.DRO_E2E_BASE_URL || '').replace(/\/$/, '');
const token = process.env.DRO_E2E_TOKEN || '';
const options = token ? { headers: { Authorization: `Bearer ${token}` } } : {};

test('live backend health endpoint', { skip: !base }, async () => {
  const response = await fetch(`${base}/api/health`, options);
  assert.equal(response.ok, true, `health returned HTTP ${response.status}`);
  const body = await response.json();
  assert.equal(body.status, 'ok');
});

test('live frontend/static endpoint is reachable', { skip: !base }, async () => {
  const response = await fetch(base);
  assert.equal(response.ok, true, `frontend returned HTTP ${response.status}`);
  const html = await response.text();
  assert.match(html, /Downtime Response Orchestrator|<div id="root">/i);
});

test('live dashboard contract returns an object', { skip: !base }, async () => {
  const response = await fetch(`${base}/api/dashboard/assets`, options);
  assert.ok([200, 401, 403].includes(response.status), `unexpected dashboard HTTP ${response.status}`);
  if (response.ok) {
    const body = await response.json();
    assert.equal(typeof body, 'object');
    assert.ok(Array.isArray(body.assets), 'dashboard assets must be an array');
  }
});
