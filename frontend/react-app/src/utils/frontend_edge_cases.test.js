/* Focused frontend edge cases that complement the generated 1,000-case suite.
 * These tests cover modules and wiring that are difficult to validate through
 * a pure API matrix: authentication, sensor WebSockets, malformed responses,
 * and the React-level contracts that prevent stale or unauthorised actions.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const values = new Map();
globalThis.localStorage = {
  getItem: key => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key),
};
globalThis.CustomEvent = class CustomEvent {
  constructor(type) { this.type = type; }
};
const dispatched = [];
globalThis.window = {
  location: { origin: 'http://localhost:8000' },
  dispatchEvent: event => { dispatched.push(event.type); return true; },
};

const auth = await import('../api/auth.js');
const sensors = await import('../api/sensors.js');
const http = await import('../api/http.js');

function ok(body = {}) {
  return { ok: true, status: 200, async json() { return body; } };
}

function fail(status, detail, parse = true) {
  return {
    ok: false,
    status,
    async json() {
      if (!parse) throw new Error('not json');
      return detail === undefined ? {} : { detail };
    },
  };
}

async function withFetch(handler, fn) {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const call = { url: String(url), options };
    calls.push(call);
    return handler(call, calls.length - 1);
  };
  try { return await fn(calls); } finally { globalThis.fetch = previous; }
}

// 24 login cases: successful authentication preserves all server fields.
const loginUsers = ['supervisor', 'engineer', 'maintenance', 'manager', 'executive', 'ot', 'safety', 'md'];
for (let i = 0; i < 24; i += 1) {
  test(`auth login success contract ${i + 1}`, async () => {
    const username = `${loginUsers[i % loginUsers.length]}-${i}`;
    const payload = { access_token: `jwt-${i}`, username, role: 'operator', allowed_personas: [loginUsers[i % loginUsers.length]], expires_in_hours: 8 };
    await withFetch(call => {
      assert.equal(call.url, 'http://localhost:8000/api/auth/login');
      assert.equal(call.options.method, 'POST');
      assert.equal(call.options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(call.options.body), { username, password: `secret-${i}` });
      return ok(payload);
    }, async () => assert.deepEqual(await auth.loginUser(username, `secret-${i}`), payload));
  });
}

// 24 login failures: detail is surfaced and non-JSON failures remain useful.
for (let i = 0; i < 24; i += 1) {
  test(`auth login failure ${i + 1}`, async () => {
    const status = i % 2 ? 422 : 401;
    const parse = i % 3 !== 0;
    await withFetch(() => fail(status, parse ? `invalid credentials ${i}` : undefined, parse), async () => {
      await assert.rejects(
        auth.loginUser(`user-${i}`, 'bad'),
        error => error instanceof Error
          && (error.message.includes(`invalid credentials ${i}`) || error.message.includes(`HTTP ${status}`)),
      );
    });
  });
}

// 24 session rehydration cases: /me sends the bearer token and handles expiry.
for (let i = 0; i < 24; i += 1) {
  test(`auth session rehydration ${i + 1}`, async () => {
    http.setToken(`session-${i}`);
    if (i % 4 !== 0) {
      const me = { username: `user-${i}`, role: 'operator' };
      await withFetch(call => {
        assert.equal(call.url, 'http://localhost:8000/api/auth/me');
        assert.equal(call.options.headers.Authorization, `Bearer session-${i}`);
        return ok(me);
      }, async () => assert.deepEqual(await auth.getMe(), me));
    } else {
      await withFetch(() => fail(401, 'expired'), async () => {
        await assert.rejects(auth.getMe(), /Session expired or invalid/);
      });
      assert.equal(http.getToken(), null);
    }
  });
}

// 30 sensor cases: URL encoding, replacement cleanup, valid data, and bad JSON.
const socketInstances = [];
globalThis.WebSocket = class FakeWebSocket {
  constructor(url) { this.url = url; this.closed = false; socketInstances.push(this); }
  close() { this.closed = true; }
};
for (let i = 0; i < 30; i += 1) {
  test(`sensor WebSocket case ${i + 1}`, () => {
    const asset = i % 2 ? `P-208/zone ${i}` : `M-104-${i}`;
    const received = [];
    sensors.connectSensorWS(asset, data => received.push(data));
    const current = socketInstances.at(-1);
    assert.equal(current.url, `ws://localhost:8000/ws/sensors/${encodeURIComponent(asset)}`);
    current.onmessage({ data: JSON.stringify({ asset_id: asset, vibration: i / 10 }) });
    assert.deepEqual(received, [{ asset_id: asset, vibration: i / 10 }]);
    current.onmessage({ data: '{malformed' });
    assert.equal(received.length, 1, 'malformed sensor data must be ignored');
    if (i > 0) assert.equal(socketInstances.at(-2).closed, true, 'asset switch must close previous socket');
  });
}

// 20 transport cases: custom headers survive, network failures propagate, and 401 clears auth.
for (let i = 0; i < 20; i += 1) {
  test(`HTTP transport edge case ${i + 1}`, async () => {
    http.setToken(`transport-${i}`);
    if (i % 5 === 0) {
      await withFetch(() => { throw new Error(`network down ${i}`); }, async () => {
        await assert.rejects(http.authedFetch('/api/network'), new RegExp(`network down ${i}`));
      });
      assert.equal(http.getToken(), `transport-${i}`);
    } else if (i % 5 === 1) {
      await withFetch(() => fail(401, 'expired'), async calls => {
        await http.authedFetch('/api/protected', { headers: { 'X-Trace': `trace-${i}` } });
        assert.equal(calls[0].options.headers['X-Trace'], `trace-${i}`);
      });
      assert.equal(http.getToken(), null);
      assert.equal(dispatched.at(-1), 'dro-auth-expired');
    } else {
      await withFetch(call => {
        assert.equal(call.options.headers.Authorization, `Bearer transport-${i}`);
        assert.equal(call.options.headers['X-Trace'], `trace-${i}`);
        return ok({ i });
      }, async () => {
        const resp = await http.authedFetch('/api/protected', { headers: { 'X-Trace': `trace-${i}` } });
        assert.equal(resp.ok, true);
      });
    }
  });
}

// 12 static wiring guards: protect the React integration points from accidental removal.
const chatSource = await readFile(new URL('../components/ChatView/ChatView.jsx', import.meta.url), 'utf8');
const contextSource = await readFile(new URL('../context/AppContext.jsx', import.meta.url), 'utf8');
const personaSource = await readFile(new URL('../data/personas.js', import.meta.url), 'utf8');
const wiringChecks = [
  ['chat uses backend API helper', /askChat\(/],
  ['chat carries conversation id', /conversationIdRef/],
  ['chat renders read-only HITL notice', /HITLReadOnlyNotice/],
  ['chat checks persona permission', /canResolveHITL/],
  ['chat uses escalation helper', /activeEscalationStep/],
  ['chat updates escalation countdown', /remainingSeconds/],
  ['chat forwards HITL actions', /forwardHITL/],
  ['chat resolves executor actions', /runExecutor/],
  ['context loads work orders from API', /fetchWorkOrders/],
  ['chat applies persisted asset lifecycle state', /dro-asset-status/],
  ['context polls backend work orders', /pollInterval/],
  ['persona definitions are data-driven', /Supervisor|Engineer|Maintenance|Manager|Executive|OT|Safety/],
];
for (const [label, pattern] of wiringChecks) {
  test(`React wiring guard: ${label}`, () => {
    const source = label.startsWith('context') ? contextSource : label.startsWith('persona') ? personaSource : chatSource;
    assert.match(source, pattern);
  });
}
