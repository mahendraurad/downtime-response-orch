import { API } from '../config/api';

export async function runRealPipeline(scenario, rowIndex, persona, demoHITL) {
  const payload = { scenario, row_index: rowIndex ?? -1, persona };
  if (demoHITL) payload.demo_hitl = true;
  const resp = await fetch(`${API}/api/pipeline/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signal: {}, ...payload }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function resolveHITLRemediation(runId, action, persona) {
  const resp = await fetch(`${API}/api/pipeline/hitl/remediation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function resolveHITLMonitoring(runId, action, persona) {
  const resp = await fetch(`${API}/api/pipeline/hitl/monitoring`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function resolveHITLDiagnosis(runId, action, persona) {
  const resp = await fetch(`${API}/api/pipeline/hitl/diagnosis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function resolveHITLKnowledge(runId, action, persona) {
  const resp = await fetch(`${API}/api/pipeline/hitl/knowledge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function runExecutor(recommendation, approved) {
  const resp = await fetch(`${API}/api/executor/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recommendation, approved }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function getNotifCounts() {
  const resp = await fetch(`${API}/api/notifications/counts`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function getNotifs(personaId) {
  const resp = await fetch(`${API}/api/notifications/${personaId}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function markNotifRead(personaId) {
  const resp = await fetch(`${API}/api/notifications/${personaId}/read`, { method: 'POST' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}
