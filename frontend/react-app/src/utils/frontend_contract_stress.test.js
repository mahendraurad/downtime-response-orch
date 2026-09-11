/* Deterministic frontend contract/stress suite.
 *
 * These are generated from meaningful input matrices, not copied assertions:
 * 1,000 cases exercise timer boundaries, persona escalation, API payloads,
 * authentication/error handling, and durable HITL/notification contracts.
 * No browser, backend, dataset, or network is required.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const storage = new Map();
globalThis.localStorage = {
  getItem: key => storage.has(key) ? storage.get(key) : null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
};
globalThis.CustomEvent = class CustomEvent {
  constructor(type) { this.type = type; }
};
globalThis.window = {
  location: { origin: 'http://localhost:8000' },
  dispatchEvent: () => true,
};

const timer = await import('./escalationTimer.js');
const http = await import('../api/http.js');
const chatApi = await import('../api/chat.js');
const pipelineApi = await import('../api/pipeline.js');
const workOrderApi = await import('../api/workOrders.js');
const dashboardApi = await import('../api/dashboard.js');

function register(name, fn) {
  test(name, { concurrency: false }, fn);
}

function response(status = 200, body = { case_id: 'CASE-STRESS' }) {
  return {
    status,
    ok: status >= 200 && status < 300,
    async json() { return body; },
  };
}

async function withFetch(handler, fn) {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return handler(calls[calls.length - 1]);
  };
  try { return await fn(calls); } finally { globalThis.fetch = previous; }
}

http.setToken('stress-token');

// 240 cases: every second-level countdown boundary, including expiry.
for (let i = 0; i < 240; i += 1) {
  const offset = i - 120;
  register(`timer remainingSeconds boundary ${i + 1}`, () => {
    const now = Date.parse('2026-09-11T10:00:00.000Z');
    const deadline = new Date(now + offset * 1000).toISOString();
    assert.equal(timer.remainingSeconds(deadline, now), Math.max(0, offset));
  });
}

// 120 cases: format invariants over hours/minutes/seconds rollover values.
for (let i = 0; i < 120; i += 1) {
  const value = i * 37;
  register(`timer format rollover ${i + 1}`, () => {
    const h = String(Math.floor(value / 3600)).padStart(2, '0');
    const m = String(Math.floor((value % 3600) / 60)).padStart(2, '0');
    const s = String(value % 60).padStart(2, '0');
    assert.equal(timer.formatCountdown(value), `${h}:${m}:${s}`);
  });
}

// 140 cases: active/inactive/final-authority escalation matrix.
const personas = ['supervisor', 'engineer', 'maintenance', 'manager', 'executive', 'ot', 'safety'];
for (let i = 0; i < 140; i += 1) {
  const persona = personas[i % personas.length];
  const stage = i % 5;
  const mode = i % 4;
  register(`escalation matrix case ${i + 1}`, () => {
    const steps = [
      { from_persona_id: 'supervisor', to_persona_id: 'manager' },
      { from_persona_id: 'manager', to_persona_id: 'executive' },
    ];
    const escalation = { status: mode === 0 ? 'active' : 'final_authority', steps };
    const actual = timer.activeEscalationStep(escalation, stage, persona);
    if (mode !== 0 || stage > 1 || persona === 'executive') assert.equal(actual, null);
    else assert.deepEqual(actual, steps[stage]);
  });
}

// 350 cases: success contracts for every critical frontend API operation.
const successCases = [
  { name: 'askChat', call: i => chatApi.askChat({ message: `status ${i}`, persona: personas[i % personas.length], conversationId: `C-${i}/M-104` }), method: 'POST', path: '/api/chat' },
  { name: 'fetchChatHistory', call: i => chatApi.fetchChatHistory(`C-${i}/M-104`), method: 'GET', path: '/api/chat/history/' },
  { name: 'runRealPipeline', call: i => pipelineApi.runRealPipeline('outer_race_fault', i, personas[i % personas.length], i % 2 === 0), method: 'POST', path: '/api/pipeline/run' },
  { name: 'resolveHITLRemediation', call: i => pipelineApi.resolveHITLRemediation(`R-${i}`, 'IMPUTE', personas[i % personas.length]), method: 'POST', path: '/api/pipeline/hitl/remediation' },
  { name: 'resolveHITLMonitoring', call: i => pipelineApi.resolveHITLMonitoring(`R-${i}`, 'CONFIRM', personas[i % personas.length]), method: 'POST', path: '/api/pipeline/hitl/monitoring' },
  { name: 'resolveHITLDiagnosis', call: i => pipelineApi.resolveHITLDiagnosis(`R-${i}`, 'CONFIRM', personas[i % personas.length]), method: 'POST', path: '/api/pipeline/hitl/diagnosis' },
  { name: 'resolveHITLKnowledge', call: i => pipelineApi.resolveHITLKnowledge(`R-${i}`, 'ACCEPT_EMPTY', personas[i % personas.length]), method: 'POST', path: '/api/pipeline/hitl/knowledge' },
  { name: 'resolveHITLAdvisory', call: i => pipelineApi.resolveHITLAdvisory(`R-${i}`, 'ACCEPT', personas[i % personas.length], ''), method: 'POST', path: '/api/pipeline/hitl/advisory' },
  { name: 'getPendingHITL', call: i => pipelineApi.getPendingHITL(personas[i % personas.length], i % 2 ? 'diagnosis' : ''), method: 'GET', path: '/api/pipeline/hitl/pending' },
  { name: 'getHITLStatus', call: i => pipelineApi.getHITLStatus(`R-${i}`, personas[i % personas.length]), method: 'GET', path: '/api/pipeline/hitl/' },
  { name: 'forwardHITL', call: i => pipelineApi.forwardHITL(`R-${i}`, 'diagnosis', 'supervisor', 'engineer', 'review', 'P-208', `C-${i}`), method: 'POST', path: '/api/pipeline/hitl/forward' },
  { name: 'runExecutor', call: i => pipelineApi.runExecutor({ case_id: `CASE-${i}`, asset_id: 'M-104' }, i % 2 === 0, personas[i % personas.length]), method: 'POST', path: '/api/executor/run' },
  { name: 'rejectRecommendation', call: i => pipelineApi.rejectRecommendation({ case_id: `CASE-${i}`, asset_id: 'M-104', condition: { fault_type: 'outer_race_fault' }, conversation_id: `C-${i}` }, 'second_opinion', 'reviewed', personas[i % personas.length]), method: 'POST', path: '/api/recommendations/reject' },
  { name: 'getNotifCounts', call: () => pipelineApi.getNotifCounts(), method: 'GET', path: '/api/notifications/counts', auth: false },
  { name: 'getNotifs', call: i => pipelineApi.getNotifs(personas[i % personas.length]), method: 'GET', path: '/api/notifications/', auth: false },
  { name: 'postPersonaNotification', call: i => pipelineApi.postPersonaNotification(personas[i % personas.length], { title: `Review ${i}`, message: 'P-208', type: 'hitl_monitoring' }), method: 'POST', path: '/api/notifications' },
  { name: 'markNotifRead', call: i => pipelineApi.markNotifRead(personas[i % personas.length]), method: 'POST', path: '/api/notifications/', auth: false, bodyRequired: false },
  { name: 'patchWorkOrder', call: i => workOrderApi.patchWorkOrder(`WO-${i}`, { status: i % 2 ? 'Rejected' : 'Closed', updated_by: 'Plant Supervisor' }), method: 'PATCH', path: '/api/workorders/' },
  { name: 'fetchWorkOrders', call: () => workOrderApi.fetchWorkOrders(), method: 'GET', path: '/api/workorders' },
  { name: 'fetchDashboardAssets', call: () => dashboardApi.fetchDashboardAssets(), method: 'GET', path: '/api/dashboard/assets' },
];
for (let i = 0; i < 350; i += 1) {
  const item = successCases[i % successCases.length];
  register(`API success contract ${item.name} ${i + 1}`, async () => {
    const result = await withFetch(() => response(), async calls => {
      const data = await item.call(i);
      assert.equal(data.case_id, 'CASE-STRESS');
      return calls[0];
    });
    assert.equal(result.options.method || 'GET', item.method);
    assert.ok(result.url.includes(item.path), `${result.url} should include ${item.path}`);
    if (item.auth !== false) {
      assert.equal(result.options.headers.Authorization, 'Bearer stress-token');
    } else {
      assert.equal(result.options.headers?.Authorization, undefined);
    }
    if (item.method !== 'GET' && item.bodyRequired !== false) {
      assert.ok(result.options.body, `${item.name} must send a JSON body`);
      assert.doesNotThrow(() => JSON.parse(result.options.body));
    }
  });
}

// 100 cases: every error path must surface a useful exception, never a fake result.
const errorCases = [
  { name: 'chat', call: () => chatApi.askChat({ message: 'bad', persona: 'supervisor' }), path: '/api/chat' },
  { name: 'pipeline', call: () => pipelineApi.runRealPipeline('missing', -1, 'supervisor', false), path: '/api/pipeline/run' },
  { name: 'hitl', call: () => pipelineApi.resolveHITLDiagnosis('missing', 'CONFIRM', 'engineer'), path: '/api/pipeline/hitl/diagnosis' },
  { name: 'workorder', call: () => workOrderApi.patchWorkOrder('missing', { status: 'Closed' }), path: '/api/workorders/' },
  { name: 'notification', call: () => pipelineApi.postPersonaNotification('engineer', { message: 'bad' }), path: '/api/notifications' },
];
for (let i = 0; i < 100; i += 1) {
  const item = errorCases[i % errorCases.length];
  register(`API graceful error ${item.name} ${i + 1}`, async () => {
    let calls;
    await assert.rejects(
      withFetch(() => response(422, { detail: `invalid request ${i}` }), recorded => {
        calls = recorded;
        return item.call();
      }),
      error => /invalid request|HTTP 422|failed/i.test(error.message),
    );
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.includes(item.path), `${calls[0].url} should include ${item.path}`);
  });
}

// 50 cases: token lifecycle and 401 handling.
for (let i = 0; i < 50; i += 1) {
  register(`authentication lifecycle ${i + 1}`, async () => {
    http.setToken(`token-${i}`);
    const result = await withFetch(() => response(200), async calls => {
      await http.authedFetch(`/api/auth-check/${i}`);
      return calls[0];
    });
    assert.equal(result.options.headers.Authorization, `Bearer token-${i}`);
    if (i % 10 === 0) {
      await withFetch(() => response(401, { detail: 'expired' }), async () => {
        await http.authedFetch(`/api/auth-expired/${i}`);
      });
      assert.equal(http.getToken(), null);
    }
  });
}
