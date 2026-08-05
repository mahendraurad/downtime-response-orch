import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activeEscalationStep,
  formatCountdown,
  formatEscalationTime,
  remainingSeconds,
} from './escalationTimer.js';

test('remainingSeconds counts down and clamps expired deadlines', () => {
  const now = Date.parse('2026-08-04T10:00:00Z');
  assert.equal(remainingSeconds('2026-08-04T10:15:00Z', now), 900);
  assert.equal(remainingSeconds('2026-08-04T09:59:00Z', now), 0);
  assert.equal(remainingSeconds('invalid', now), null);
});

test('formatCountdown uses a stable hours-minutes-seconds display', () => {
  assert.equal(formatCountdown(3661), '01:01:01');
  assert.equal(formatCountdown(0), '00:00:00');
  assert.equal(formatCountdown(null), '--:--:--');
});

test('activeEscalationStep selects only active configured steps', () => {
  const steps = [{ to_persona_id: 'manager' }, { to_persona_id: 'executive' }];
  assert.deepEqual(activeEscalationStep({ status: 'active', steps }, 1), steps[1]);
  assert.equal(activeEscalationStep({ status: 'final_authority', steps }, 0), null);
  assert.equal(activeEscalationStep({ status: 'active', steps }, 4), null);
});

test('formatEscalationTime exposes an exact readable deadline', () => {
  const formatted = formatEscalationTime('2026-08-04T10:15:00Z', 'en-GB');
  assert.match(formatted, /04 Aug 2026/);
  assert.match(formatted, /\d{2}:\d{2}:\d{2}/);
  assert.equal(formatEscalationTime('invalid', 'en-GB'), 'deadline unavailable');
});
