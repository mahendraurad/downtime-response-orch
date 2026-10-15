import { API } from '../config/api.js';
import { authedFetch } from './http.js';

async function throwApiError(resp) {
  let detail;
  try { detail = (await resp.json()).detail; } catch (_) { /* ignore parse failure */ }
  throw new Error(detail || `HTTP ${resp.status}`);
}

export async function runRealPipeline(scenario, rowIndex, persona, demoHITL) {
  const payload = { scenario, row_index: rowIndex ?? -1, persona };
  if (demoHITL) payload.demo_hitl = true;
  const resp = await authedFetch(`${API}/api/pipeline/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signal: {}, ...payload }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function resolveHITLRemediation(runId, action, persona) {
  const resp = await authedFetch(`${API}/api/pipeline/hitl/remediation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function resolveHITLMonitoring(runId, action, persona) {
  const resp = await authedFetch(`${API}/api/pipeline/hitl/monitoring`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function resolveHITLDiagnosis(runId, action, persona) {
  const resp = await authedFetch(`${API}/api/pipeline/hitl/diagnosis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function resolveHITLKnowledge(runId, action, persona) {
  const resp = await authedFetch(`${API}/api/pipeline/hitl/knowledge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function resolveHITLAdvisory(runId, action, persona, revisedNote) {
  const resp = await authedFetch(`${API}/api/pipeline/hitl/advisory`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, action, persona, revised_note: revisedNote || null }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function getPendingHITL(persona, gate = '') {
  const params = new URLSearchParams({ persona });
  if (gate) params.set('gate', gate);
  const resp = await authedFetch(`${API}/api/pipeline/hitl/pending?${params}`);
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function getHITLStatus(runId, persona) {
  const params = new URLSearchParams({ persona });
  const resp = await authedFetch(`${API}/api/pipeline/hitl/${encodeURIComponent(runId)}?${params}`);
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function forwardHITL(runId, gate, fromPersona, toPersona, rationale = '', assetId = '', conversationId = null, escalationStageIndex = null) {
  const resp = await authedFetch(`${API}/api/pipeline/hitl/forward`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      run_id: runId, gate, from_persona: fromPersona,
      to_persona: toPersona || null, rationale,
      asset_id: assetId || null, conversation_id: conversationId || null,
      escalation_stage_index: escalationStageIndex == null ? null : Number(escalationStageIndex),
    }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function runExecutor(recommendation, approved, persona = 'supervisor') {
  const resp = await authedFetch(`${API}/api/executor/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recommendation, approved, persona }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function getNotifCounts() {
  const resp = await fetch(`${API}/api/notifications/counts`);
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function rejectRecommendation(recommendation, reasonCode, freeText = '', persona = 'supervisor') {
  const response = await authedFetch(`${API}/api/recommendations/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      case_id: recommendation.case_id,
      asset_id: recommendation.asset_id,
      fault_mode: recommendation.condition?.fault_type || 'undetermined',
      reason_code: reasonCode,
      free_text: freeText,
      persona,
      conversation_id: recommendation.conversation_id || null,
      hitl_run_id: recommendation.hitl_run_id || null,
    }),
  });
  if (!response.ok) await throwApiError(response);
  return response.json();
}

export async function getNotifs(personaId) {
  const resp = await fetch(`${API}/api/notifications/${personaId}`);
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export function notificationText(msg = {}) {
  const supplied = String(msg.message || msg.text || '').trim();
  if (supplied) return supplied;
  const html = String(msg.html || '');
  if (!html) return 'A new DRO action requires your attention.';
  return html
    .replace(/<br\s*\/?\s*>/gi, ' ')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 2000) || 'A new DRO action requires your attention.';
}

export async function postPersonaNotification(personaId, msg = {}) {
  const resp = await authedFetch(`${API}/api/notifications`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      persona_id: personaId,
      title: msg.title || 'DRO notification',
      // Cross-persona cards historically supplied rich `html` only. Persist a
      // readable summary as well so a refresh/different browser does not turn
      // the hand-off or result into a generic, context-free notification.
      message: notificationText(msg),
      notification_type: msg.type || 'agent',
      asset_id: msg.asset_id || msg.data?.asset_id || msg.rec?.asset_id || '',
      work_order_id: msg.work_order_id || msg.rec?.work_order_id || '',
      run_id: msg.run_id || msg.data?.run_id || '',
      metadata: msg.metadata || {},
    }),
  });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}

export async function markNotifRead(personaId) {
  const resp = await fetch(`${API}/api/notifications/${personaId}/read`, { method: 'POST' });
  if (!resp.ok) await throwApiError(resp);
  return resp.json();
}
