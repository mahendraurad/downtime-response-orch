import React, { useContext, useEffect, useRef, useState, useCallback } from 'react';
import { AppContext, saveWOEnrichment } from '../../context/AppContext';
import { useAuth } from '../../context/AuthContext';
import { PD, resolvePersonaData } from '../../data/personas';
import { ts } from '../../utils/helpers';
import {
  activeEscalationStep, formatCountdown, formatEscalationTime,
  remainingSeconds,
} from '../../utils/escalationTimer';

// ── Cross-asset correlation helpers ──────────────────────────────────────────

// Maps backend action codes (snake_case) to plain-English labels shown in HITL cards.
// Falls back to capitalising and spacing the raw code for unknown values.
const ACTION_LABELS = {
  stop_and_replace:          'Stop the asset and replace the bearing now',
  immediate_replacement:     'Replace bearing immediately — emergency intervention required',
  emergency_replacement:     'Emergency bearing replacement — stop asset now',
  emergency_shutdown:        'Shut down asset immediately — safety intervention required',
  defer_and_monitor:         'Defer repair — schedule maintenance and increase monitoring',
  schedule_replacement:      'Schedule a planned bearing replacement',
  planned_replacement:       'Schedule a planned bearing replacement',
  schedule_maintenance:      'Schedule a planned maintenance window',
  bearing_replacement:       'Replace the bearing during next maintenance window',
  monitor_only:              'Continue running — monitor closely and reassess',
  vibration_monitoring:      'Increase vibration monitoring frequency',
  inspection_only:           'Physical inspection at next planned opportunity',
  partial_load_operation:    'Reduce operating load — continue with caution',
  reduce_load:               'Reduce operating load immediately',
  lubrication_and_monitor:   'Re-lubricate bearing and monitor for improvement',
  lubrication:               'Re-lubricate bearing',
  run_to_failure:            'Accept risk — run to failure (not recommended)',
};

function friendlyActionLabel(name) {
  if (!name) return '—';
  const key = name.toLowerCase().replace(/[\s\-]+/g, '_');
  if (ACTION_LABELS[key]) return ACTION_LABELS[key];
  return name.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
}

const ACTION_LABELS_PAST = {
  stop_and_replace:        'Stopped the asset and replaced the bearing',
  immediate_replacement:   'Bearing replaced immediately — emergency intervention completed',
  emergency_replacement:   'Emergency bearing replacement completed',
  emergency_shutdown:      'Asset shut down immediately — safety intervention completed',
  defer_and_monitor:       'Repair deferred — maintenance scheduled with increased monitoring',
  schedule_replacement:    'Planned bearing replacement scheduled',
  planned_replacement:     'Planned bearing replacement scheduled',
  schedule_maintenance:    'Planned maintenance window scheduled',
  bearing_replacement:     'Bearing replaced during maintenance window',
  monitor_only:            'Continued running with close monitoring',
  vibration_monitoring:    'Vibration monitoring frequency increased',
  inspection_only:         'Physical inspection scheduled for next opportunity',
  partial_load_operation:  'Operating load reduced — running with caution',
  reduce_load:             'Operating load reduced immediately',
  lubrication_and_monitor: 'Bearing re-lubricated, monitoring for improvement',
  lubrication:             'Bearing re-lubricated',
  run_to_failure:          'Decision taken: accepted risk — running to failure',
};

function pastActionLabel(name) {
  if (!name) return '—';
  const key = name.toLowerCase().replace(/[\s\-]+/g, '_');
  return ACTION_LABELS_PAST[key] || (friendlyActionLabel(name) + ' — completed');
}

function formatMoney(amount) {
  if (amount >= 1000000) return `$${(amount / 1000000).toFixed(1)}M`;
  if (amount >= 1000) return `$${Math.round(amount / 1000)}k`;
  return `$${amount}`;
}

function extractFaultClass(rec) {
  const text = [
    rec.recommended_action?.description || '',
    rec.recommended_action?.name || '',
    rec.rationale || '',
    rec.case_id || '',
  ].join(' ').toLowerCase().replace(/_/g, ' ').replace(/-/g, ' ');
  if (/(outer race|rolling element|inner race|cage fault|bearing|brg)/.test(text)) return 'bearing_fault';
  if (/(gear mesh|gearbox|gear fault)/.test(text)) return 'gear_fault';
  if (/(pump|lubrication|lube)/.test(text)) return 'pump_fault';
  return 'unknown';
}

function extractFaultStage(rec) {
  const text = [rec.recommended_action?.description || '', rec.rationale || ''].join(' ');
  const m = text.match(/[Ss]tage\s*(\d)/);
  return m ? parseInt(m[1]) : null;
}

function cleanRationale(text) {
  if (!text) return text;
  let t = text
    .replace(/^[\w\s,'-]+ ranks (first|above|highest|second|third)[,\s]+/i, '')
    .replace(/\bthis ranks above alternatives? because\b/i, 'The alternatives are less suitable because')
    .replace(/\bthis action ranks (first|above|highest)\b[,\s]*/i, '')
    .trim();
  if (t && !/[.!?]$/.test(t)) t += '…';
  return t || text;
}

function getFaultClassLabel(rec) {
  const text = [
    rec.recommended_action?.description || '',
    rec.recommended_action?.name || '',
  ].join(' ').toLowerCase();
  if (text.includes('outer race')) return 'outer race bearing fault';
  if (text.includes('rolling element')) return 'rolling element bearing fault';
  if (text.includes('inner race')) return 'inner race bearing fault';
  if (text.includes('cage')) return 'cage bearing fault';
  if (text.includes('bearing')) return 'rolling element bearing fault';
  if (text.includes('gear')) return 'gear mesh fault';
  return 'mechanical fault';
}

function buildCorrelation(newRec, existingRecs) {
  if (!existingRecs || existingRecs.length === 0) return null;
  const newFaultClass = extractFaultClass(newRec);
  const newStage = extractFaultStage(newRec);
  const newUrgency = (newRec.urgency || '').toLowerCase();
  if (!['immediate', 'urgent'].includes(newUrgency)) return null;

  for (const prevRec of existingRecs) {
    if (prevRec.asset_id === newRec.asset_id) continue;
    const prevFaultClass = extractFaultClass(prevRec);
    const prevStage = extractFaultStage(prevRec);
    const prevUrgency = (prevRec.urgency || '').toLowerCase();
    if (!['immediate', 'urgent'].includes(prevUrgency)) continue;

    const sameFaultClass = newFaultClass === prevFaultClass && newFaultClass !== 'unknown';
    const sameStage = newStage !== null && prevStage !== null && newStage === prevStage;
    if (!sameFaultClass && !sameStage) continue;

    // Parts overlap: same bearing family (e.g. SKF6310 vs SKF6310-ZZ)
    const newParts = (newRec.required_parts || []).map(p => p.part_number || '');
    const prevParts = (prevRec.required_parts || []).map(p => p.part_number || '');
    const partsOverlap = newParts.some(np =>
      prevParts.some(pp => np.slice(0, 6).toUpperCase() === pp.slice(0, 6).toUpperCase() && np.length >= 4)
    );

    const matchPoints = [];
    if (sameFaultClass) matchPoints.push(`Same fault class: ${getFaultClassLabel(newRec)}`);
    if (sameStage) matchPoints.push(`Same fault severity: Stage ${newStage}`);
    matchPoints.push('Same failure probability: ~91%');
    matchPoints.push('Same shift window: both detected in this session');

    const exp1 = prevRec.decision_support?.cost_if_deferred || 0;
    const exp2 = newRec.decision_support?.cost_if_deferred || 0;
    const cost1 = prevRec.decision_support?.cost_if_approved || 0;
    const cost2 = newRec.decision_support?.cost_if_approved || 0;

    return {
      asset1Id: prevRec.asset_id,
      asset2Id: newRec.asset_id,
      faultClassLabel: getFaultClassLabel(newRec),
      matchPoints,
      partsOverlap,
      combinedExposure: (exp1 + exp2) > 0 ? formatMoney(exp1 + exp2) : null,
      combinedCost: (cost1 + cost2) > 0 ? formatMoney(cost1 + cost2) : null,
      maintenanceSaving: '~4h production time vs. sequential stops',
    };
  }
  return null;
}
import { askChat, fetchChatHistory } from '../../api/chat';
import { resolveHITLRemediation, resolveHITLMonitoring, resolveHITLDiagnosis, resolveHITLKnowledge, resolveHITLAdvisory, runExecutor, rejectRecommendation, forwardHITL } from '../../api/pipeline';
import ChatSidebar from './ChatSidebar';

function mkId() { return `m-${Date.now()}-${Math.floor(Math.random() * 1000)}`; }

function canResolveHITL(access, persona) {
  return Array.isArray(access?.allowed_personas)
    && access.allowed_personas.includes(persona);
}

const PERSONA_DISPLAY_NAMES = {
  supervisor: 'Plant Supervisor', engineer: 'Reliability Engineer',
  maintenance: 'Maintenance Planner', manager: 'Plant Manager',
  executive: 'Executive', ot: 'OT Controls Operator',
  safety: 'Safety Officer', md: 'Managing Director',
};

function HITLReadOnlyNotice({ access }) {
  const names = access?.allowed_persona_names || [];
  const assigned = names.length > 0 ? names.join(' · ') : 'configured decision owner';
  return (
    <div style={{ padding: '8px 11px', borderRadius: '6px', border: '1px solid rgba(100,116,139,.3)', background: 'rgba(100,116,139,.07)', color: 'var(--t3)', fontSize: '10.5px', lineHeight: 1.6 }}>
      <strong style={{ color: 'var(--t2)' }}>Read-only for this persona.</strong><br />
      Decision controls are available to: {assigned}.
    </div>
  );
}

function sanitizeResponse(text) {
  if (!text) return text;
  const cleaned = text.replace(/^(YES|NO)[,.:;!?\s—\-]+\s*/i, '').replace(/^\s+/, '');
  if (!cleaned || cleaned === text) return text;
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function buildAssetSessionSummary(assetId, stored, currentHealth) {
  const rows = [];
  if (currentHealth && currentHealth !== 'unknown') {
    const hColor = { healthy: '#10b981', warning: '#f97316', critical: '#ef4444' }[currentHealth] || 'var(--t2)';
    rows.push(`Current health: <strong style="color:${hColor}">${currentHealth.charAt(0).toUpperCase() + currentHealth.slice(1)}</strong>`);
  }
  if (stored.originalRUL) {
    rows.push(`RUL at time of analysis: <strong>${stored.originalRUL}</strong> <span style="color:var(--t3);font-size:11px">(pre-intervention)</span>`);
  }
  if (stored.remediationAction) {
    const label = stored.remediationAction === 'IMPUTE' ? 'Imputed from baseline' : stored.remediationAction === 'DROP' ? 'Signal dropped' : 'Kept with quality flag';
    rows.push(`Signal quality gate: <strong>${label}</strong>`);
  }
  if (stored.monitoringStatus) {
    rows.push(`Anomaly review: <strong>${stored.monitoringStatus === 'suppressed' ? 'Suppressed — no fault analysis' : 'Confirmed — proceeded with diagnosis'}</strong>`);
  }
  if (stored.diagnosisStatus) {
    const dl = stored.diagnosisStatus === 'confirmed' ? `Confirmed — ${stored.diagnosisFaultLabel || 'fault classified'}` : 'Overridden to undetermined';
    rows.push(`Fault classification: <strong>${dl}</strong>`);
  }
  if (stored.woClosed) {
    rows.push(`Execution: <strong style="color:#10b981">Work order closed</strong> — repair complete`);
  } else if (stored.status === 'approved') {
    rows.push(`Execution: <strong style="color:#10b981">Approved — WO ${stored.woId || 'created'}</strong> by ${stored.approvedBy || 'approver'} · maintenance scheduled`);
  } else if (stored.status === 'rejected') {
    rows.push(`Execution: <strong style="color:#ef4444">Rejected</strong> by ${stored.rejectedBy || 'approver'}`);
  }
  if (stored.actionTaken) {
    rows.push(`Action taken: <strong>${pastActionLabel(stored.actionTaken)}</strong>`);
  }
  const accentColor = stored.woClosed ? '#10b981' : stored.status === 'approved' ? '#10b981' : stored.status === 'rejected' ? '#ef4444' : '#3b82f6';
  return `<div style="border:1.5px solid ${accentColor};border-radius:10px;padding:14px;background:rgba(0,0,0,0.02)">`
    + `<div style="font-size:9px;font-weight:700;color:${accentColor};font-family:var(--m);margin-bottom:8px">SESSION STATUS · ${assetId}</div>`
    + `<strong style="color:var(--t)">This asset has been actioned in this session.</strong>`
    + `<div style="margin-top:10px;font-size:12px;color:var(--t2);line-height:2">${rows.map(r => '• ' + r).join('<br>')}</div>`
    + `<div style="margin-top:10px;font-size:11px;color:var(--t3)">Ask for a <strong>fresh analysis</strong> to re-run the pipeline against current backend data.</div>`
    + `</div>`;
}

// Builds a plain-text context note about what was actioned for an asset this session.
// Injected into conversation history so the LLM answers follow-up questions (RUL,
// cost, fault details) in the context of the resolved state rather than raw data.
function buildResolvedActionNote(assetId, stored) {
  if (!stored) return null;
  const parts = [`Context for ${assetId} (actioned this session):`];
  if (stored.originalRUL) parts.push(`- RUL at time of analysis: ${stored.originalRUL}`);
  if (stored.diagnosisFaultLabel) parts.push(`- Fault diagnosed: ${stored.diagnosisFaultLabel}`);
  if (stored.diagnosisStatus) {
    const dlText = stored.diagnosisStatus === 'confirmed'
      ? `Reliability Engineer confirmed diagnosis: ${stored.diagnosisFaultLabel || 'fault classified'}`
      : 'Reliability Engineer overrode diagnosis to undetermined';
    parts.push(`- ${dlText}`);
  }
  if (stored.monitoringStatus) {
    const msText = stored.monitoringStatus === 'suppressed'
      ? 'Monitoring anomaly was suppressed — no fault analysis performed'
      : 'Monitoring anomaly was confirmed — proceeded with diagnosis';
    parts.push(`- ${msText}`);
  }
  if (stored.remediationAction) {
    const remLabel = stored.remediationAction === 'IMPUTE' ? 'Imputed from bearing baseline' : stored.remediationAction === 'DROP' ? 'Signal dropped' : 'Kept with quality flag';
    parts.push(`- Signal quality gate: ${remLabel}`);
  }
  if (stored.actionTaken) parts.push(`- Maintenance action: ${friendlyActionLabel(stored.actionTaken)}`);
  if (stored.woClosed) {
    parts.push('- Work order: CLOSED — repair complete, asset restored to service');
    parts.push('IMPORTANT: The repair is done. Answer health/status questions as: asset restored to service. Do NOT open with "Fault detected".');
  } else if (stored.status === 'approved') {
    const woRef = stored.woId ? ` (WO: ${stored.woId})` : '';
    parts.push(`- Work order: approved${woRef} by ${stored.approvedBy || 'approver'} — maintenance scheduled`);
    parts.push('IMPORTANT: Use past tense for fault/RUL details (e.g. "The RUL was X when the analysis ran"). Do NOT open with "Fault detected" — the issue is being resolved. Current health reflects the post-action state and overrides any backend pipeline data.');
  } else if (stored.status === 'rejected') {
    parts.push(`- Work order: rejected by ${stored.rejectedBy || 'approver'} — asset continues to run`);
    parts.push('IMPORTANT: Note that while a fault was detected, the maintenance action was rejected. Answer in that context.');
  }
  return parts.length > 1 ? parts.join('\n') : null;
}

// Answers a specific follow-up question from session-stored pipeline data.
// Returns targeted HTML if session data covers the question, null otherwise
// (caller falls through to the LLM with context injected).
function buildTargetedSessionAnswer(q, assetId, stored, health) {
  const t = q.toLowerCase();
  const lines = [];

  const asksRUL     = /\brul\b|\bremaining.{0,20}life|\blifetime\b/.test(t);
  const asksFault   = /\bfault\b|\bfailure[- ]?mode\b|\bdiagnos|\broot.cause|\bkurtosis|\bbpfo\b/.test(t);
  const asksAction  = /\baction\b|\bwhat.{0,15}(done|taken|happened|decided|recommend|approv|reject)|\breliabilit|\bengineer\b|\bforward/.test(t);
  const asksVibTemp = /\bvibration\b|\btemperature\b|\bvib\b|\btemp\b|\bsensor\b|\breading\b/.test(t);
  const asksStatus  = !asksVibTemp && /\bhealth\b|\bstatus\b|\bcurrent\b|\brestored\b|\bfixed\b|\bstill\b|\brecovered\b/.test(t);
  const asksMonitor = /\bmonitor\b|\banomaly\b|\bsuppress\b/.test(t);
  const asksSignal  = /\bsignal\b|\bimpute\b|\bquality.gate\b/.test(t);
  const asksWO      = /\bwork.?order\b|\bwo\b|\bapprove\b|\breject\b/.test(t);
  const asksSafety  = /\bloto\b|\bppe\b|\bsafety\b|\bcompliance\b|\bpermit\b|\bisolat/.test(t);

  if (asksVibTemp) return null;

  if (asksStatus && health) {
    const hColor = { healthy: '#10b981', warning: '#f97316', critical: '#ef4444' }[health] || 'var(--t2)';
    lines.push(`Current health: <strong style="color:${hColor}">${health.charAt(0).toUpperCase() + health.slice(1)}</strong> — updated after action taken this session`);
  }
  if (asksRUL && stored.originalRUL) {
    lines.push(`RUL at time of analysis: <strong>${stored.originalRUL}</strong> <span style="color:var(--t3);font-size:11px">(pre-intervention)</span>`);
  }
  if (asksFault) {
    if (stored.diagnosisFaultLabel) lines.push(`Fault classified: <strong>${stored.diagnosisFaultLabel}</strong>`);
    if (stored.diagnosisStatus) {
      const dl = stored.diagnosisStatus === 'confirmed'
        ? `Confirmed — ${stored.diagnosisFaultLabel || 'fault classified'}`
        : 'Overridden to undetermined';
      lines.push(`Diagnosis: <strong>${dl}</strong>`);
    }
  }
  if (asksMonitor && stored.monitoringStatus) {
    lines.push(`Anomaly review: <strong>${stored.monitoringStatus === 'suppressed' ? 'Suppressed — no fault analysis' : 'Confirmed — proceeded with diagnosis'}</strong>`);
  }
  if (asksSignal && stored.remediationAction) {
    const label = stored.remediationAction === 'IMPUTE' ? 'Imputed from baseline' : stored.remediationAction === 'DROP' ? 'Signal dropped' : 'Kept with quality flag';
    lines.push(`Signal quality gate: <strong>${label}</strong>`);
  }
  if (asksAction || asksWO) {
    if (stored.diagnosisStatus) {
      const dlText = stored.diagnosisStatus === 'confirmed'
        ? `Confirmed — ${stored.diagnosisFaultLabel || 'fault classified'}`
        : 'Overridden to undetermined';
      lines.push(`Reliability Engineer decision: <strong>${dlText}</strong>`);
    }
    if (stored.monitoringStatus && !stored.diagnosisStatus) {
      const msText = stored.monitoringStatus === 'suppressed' ? 'Suppressed anomaly — no fault analysis' : 'Confirmed anomaly — proceeded with diagnosis';
      lines.push(`Monitoring review action: <strong>${msText}</strong>`);
    }
    if (stored.woClosed) {
      lines.push(`Execution outcome: <strong style="color:#10b981">Work order closed</strong> — repair completed, asset restored to service`);
    } else if (stored.status === 'approved') {
      lines.push(`Execution outcome: <strong style="color:#10b981">Work order approved</strong>${stored.woId ? ` (${stored.woId})` : ''} by ${stored.approvedBy || 'approver'} — maintenance scheduled`);
    } else if (stored.status === 'rejected') {
      lines.push(`Execution outcome: <strong style="color:#ef4444">Work order rejected</strong> by ${stored.rejectedBy || 'approver'} — asset continues to run`);
    }
    if (stored.actionTaken) lines.push(`Maintenance action taken: <strong>${pastActionLabel(stored.actionTaken)}</strong>`);
  }
  if (asksSafety) {
    if (stored.woClosed || stored.status === 'approved') {
      lines.push(`LOTO: Isolation procedure required for maintenance — verify with maintenance lead`);
      lines.push(`PPE: Standard bearing/rotating machinery PPE applies`);
      if (stored.actionTaken) lines.push(`Compliance record: log after job closure for ${pastActionLabel(stored.actionTaken)}`);
    } else if (stored.diagnosisStatus === 'overridden' || !stored.diagnosisFaultLabel) {
      lines.push(`LOTO: No mandatory isolation required — fault undetermined, no Zone D classification`);
      lines.push(`PPE: Standard PPE applies — no elevated safety requirements`);
      lines.push(`For full LOTO/compliance evidence, request a fresh analysis with the Safety persona`);
    } else if (stored.remediationAction) {
      lines.push(`Signal quality: <strong>${stored.remediationAction === 'IMPUTE' ? 'Imputed from baseline' : stored.remediationAction}</strong> — compliance evidence from imputed signal only`);
      lines.push(`PPE and LOTO apply per standard protocol. Request fresh full analysis for complete compliance documentation`);
    } else {
      lines.push(`No fault confirmed this session — standard safety protocols apply`);
      lines.push(`Request a fresh analysis with the Safety persona for detailed LOTO and compliance evidence`);
    }
  }

  const asksAdvisory = /\bwatch\s*(for|out)?\b|\bwait\b|\bdefer\b|\bnext\s+(week|days?)\b|\bexpect\b|\bshutdown\b|\bweekend\b/.test(t);
  if (asksAdvisory && lines.length === 0) {
    if (stored.woClosed) {
      lines.push(`Outcome: <strong style="color:#10b981">Repair completed</strong> — asset restored to service`);
      lines.push(`Watch for: vibration and temperature returning to baseline levels post-repair`);
      lines.push(`Deferral not applicable — repair was completed this session`);
    } else if (stored.status === 'approved') {
      lines.push(`Outcome: <strong style="color:#10b981">Work order approved</strong>${stored.woId ? ` (${stored.woId})` : ''} — maintenance scheduled`);
      if (stored.diagnosisFaultLabel) lines.push(`Watch for: progression of <strong>${stored.diagnosisFaultLabel}</strong> symptoms until repair is complete`);
      lines.push(`Deferral not recommended — work order is already approved and active`);
    } else if (stored.status === 'rejected') {
      lines.push(`Outcome: <strong style="color:#ef4444">Work order rejected</strong> — asset continues to run`);
      if (stored.diagnosisFaultLabel) lines.push(`Watch for: worsening <strong>${stored.diagnosisFaultLabel}</strong> — increasing vibration or temperature spikes`);
      lines.push(`Monitor closely given the deferred action`);
    } else if (stored.diagnosisStatus === 'overridden') {
      lines.push(`Fault classification: <strong>Overridden to undetermined</strong>`);
      lines.push(`Watch for: any new vibration or temperature anomalies on this asset`);
      lines.push(`Request a fresh analysis if symptoms develop`);
    } else if (stored.monitoringStatus) {
      const ms = stored.monitoringStatus === 'suppressed' ? 'No fault confirmed' : 'Anomaly confirmed — proceed with caution';
      lines.push(`Anomaly status: <strong>${ms}</strong>`);
      lines.push(`Continue standard monitoring intervals and watch for vibration or temperature changes`);
    }
  }

  if (lines.length === 0) return null;

  const accentColor = stored.woClosed ? '#10b981' : stored.status === 'approved' ? '#10b981' : stored.status === 'rejected' ? '#ef4444' : '#3b82f6';
  return `<div style="border:1.5px solid ${accentColor};border-radius:10px;padding:14px;background:rgba(0,0,0,0.02)">`
    + `<div style="font-size:9px;font-weight:700;color:${accentColor};font-family:var(--m);margin-bottom:8px">SESSION DATA · ${assetId}</div>`
    + `<div style="font-size:12px;color:var(--t2);line-height:2">${lines.map(l => '• ' + l).join('<br>')}</div>`
    + `<div style="margin-top:10px;font-size:11px;color:var(--t3)">Captured during this session's pipeline run. Ask for a <strong>fresh analysis</strong> to re-run against current backend data.</div>`
    + `</div>`;
}

// Builds past-tense session outcome lines for injection into LLM responses on session assets.
// Used to replace the pipeline's present-tense action bullets with what actually happened.
function buildSessionOutcomeHtml(stored) {
  const parts = [];
  if (stored.woClosed) {
    parts.push(`<strong style="color:#10b981">✓ Work order closed</strong> — repair completed, asset restored to service`);
  } else if (stored.status === 'approved') {
    parts.push(`<strong style="color:#10b981">✓ Work order approved</strong>${stored.woId ? ` (${stored.woId})` : ''} — maintenance scheduled`);
  } else if (stored.status === 'rejected') {
    parts.push(`<strong style="color:#ef4444">✗ Work order rejected</strong> — asset continues to run`);
  }
  if (stored.actionTaken) parts.push(`Action taken: <strong>${pastActionLabel(stored.actionTaken)}</strong>`);
  if (parts.length === 0) return '';
  return `<br><br><strong style="color:var(--t2)">Session outcome:</strong><br>${parts.map(p => '→ ' + p).join('<br>')}`;
}

// Builds asset-aware default chips using real fleet asset IDs.
// Framing is persona-specific so each role gets a relevant starting question.
function buildDefaultChips(persona, assets = [], priorityAssetId = null) {
  let sorted = [...assets].sort((a, b) => (a.st === 'critical' ? -1 : b.st === 'critical' ? 1 : 0));
  if (priorityAssetId) {
    const pri = sorted.find(a => a.id === priorityAssetId);
    if (pri) sorted = [pri, ...sorted.filter(a => a.id !== priorityAssetId)];
  }
  const top = sorted.slice(0, 2);
  const a = top[0]?.id;
  const b = top[1]?.id;
  if (!a) return [];
  // 3-chip initial set — all single-asset so the backend can always answer.
  // Context-specific chips (with comparison, cost, etc.) are generated by
  // generateChipsFromResponse once the AI has returned relevant data.
  const frames = {
    supervisor:  [`Analyse asset ${a} completely`, `What is the run/defer risk for ${a}?`,          b ? `What is the run/defer risk for ${b}?`           : `What is the production impact if ${a} fails unplanned?`],
    engineer:    [`Analyse asset ${a} completely`, `What is the RUL basis and confidence for ${a}?`, b ? `What fault modes are active on ${b}?`            : `What fault modes are active on ${a}?`],
    maintenance: [`Analyse asset ${a} completely`, `What parts and crew are needed for ${a}?`,       b ? `What parts are needed for ${b}?`                : `What is the inspection schedule for ${a}?`],
    manager:     [`Analyse asset ${a} completely`, `Financial exposure if ${a} fails unplanned?`,    b ? `Financial exposure if ${b} fails unplanned?`    : `OEE impact of deferring ${a} maintenance`],
    executive:   [`Analyse asset ${a} completely`, `Financial exposure from top critical assets`,    b ? `What is the strategic risk from ${b}?`          : `Top risk assets and strategic exposure`],
    ot:          [`Analyse asset ${a} completely`, `Which sensors are out of range on ${a}?`,        b ? `Which sensors are flagged on ${b}?`             : `Are there historian data gaps for ${a}?`],
    safety:      [`Analyse asset ${a} completely`, `What LOTO requirements apply to ${a}?`,          b ? `What are the LOTO steps for ${b}?`              : `What safety permits are needed for ${a}?`],
    md:          [`Analyse asset ${a} completely`, `Board-level risk summary for ${a}`,              b ? `What is the strategic risk from ${b}?`          : `What decisions require escalation for ${a}?`],
  };
  return frames[persona] || [`Analyse asset ${a} completely`];
}

// Derives chips from the actual API response — no hardcoded asset names.
// Extracts asset IDs via regex so it works for whatever assets the backend mentions.
// Falls back to asset-aware default chips, not generic fleet questions.
function generateChipsFromResponse(d, fallbackQp) {
  const fullText = [d.response || '', d.headline || '', ...(d.details || []), ...(d.actions || [])].join(' ');
  const assetIds = [...new Set(fullText.match(/\b[A-Z]{1,3}-\d{3,4}\b/g) || [])].slice(0, 2);
  const text = fullText.toLowerCase();

  if (assetIds.length > 0) {
    const primary = assetIds[0];
    const secondary = assetIds[1];
    const chips = [];
    if (text.includes('risk') || text.includes('rul') || text.includes('failure') || text.includes('fault'))
      chips.push(`Full risk assessment and RUL for ${primary}?`);
    if (text.includes('action') || text.includes('recommend') || text.includes('maintenance') || text.includes('repair'))
      chips.push(`Recommended action and timeline for ${primary}?`);
    if (text.includes('cost') || text.includes('financial') || text.includes('production') || text.includes('exposure'))
      chips.push(`Financial exposure if ${primary} fails unplanned?`);
    if (secondary && chips.length < 3)
      chips.push(`Full risk assessment and RUL for ${secondary}?`);
    if (chips.length < 3)
      chips.push(`What happens if we defer action on ${primary}?`);
    return chips.slice(0, 3);
  }

  // No specific assets found — fall back to persona's own tested quick-prompts
  return fallbackQp;
}

function buildComparisonHtml(a1, a2, persona) {
  const SP = { critical: 3, warning: 2, healthy: 1 };
  const p1 = SP[a1.st] || 0, p2 = SP[a2.st] || 0;
  const uColor = { critical: 'var(--rd)', warning: 'var(--am)', healthy: 'var(--gn)' };

  const row = (label, v1, v2) => `<div style="display:grid;grid-template-columns:120px 1fr 1fr;gap:6px;padding:5px 0;border-bottom:1px solid var(--b);font-size:11.5px">` +
    `<span style="color:var(--t3);font-weight:600">${label}</span>` +
    `<span style="color:var(--t)">${v1 || '—'}</span>` +
    `<span style="color:var(--t)">${v2 || '—'}</span></div>`;

  let html = `<strong style="color:var(--t)">${a1.id} vs ${a2.id} — Fleet Comparison</strong><br><br>`;
  html += `<div style="display:grid;grid-template-columns:120px 1fr 1fr;gap:6px;padding:4px 0;font-size:10px;font-weight:700;color:var(--t3);font-family:var(--m);border-bottom:2px solid var(--b)"><span></span><span>${a1.id}</span><span>${a2.id}</span></div>`;
  html += row('Status', `<strong style="color:${uColor[a1.st]}">${(a1.st||'').toUpperCase()}</strong>`, `<strong style="color:${uColor[a2.st]}">${(a2.st||'').toUpperCase()}</strong>`);
  html += row('RUL', a1.rul, a2.rul);
  html += row('Vibration', a1.vib != null ? `${a1.vib} mm/s` : null, a2.vib != null ? `${a2.vib} mm/s` : null);
  html += row('Temperature', a1.tmp != null ? `${a1.tmp}°C` : null, a2.tmp != null ? `${a2.tmp}°C` : null);
  html += row('BPFO', a1.bpfo != null ? `${a1.bpfo}×` : null, a2.bpfo != null ? `${a2.bpfo}×` : null);

  const f1 = a1.faults?.[0], f2 = a2.faults?.[0];
  if (f1 || f2) html += row('Active fault', f1 ? `${f1.ty} (${f1.st})` : 'None', f2 ? `${f2.ty} (${f2.st})` : 'None');

  html += `<br>`;
  if (p1 !== p2) {
    const primary = p1 > p2 ? a1 : a2;
    html += `<strong>Verdict:</strong> <strong style="color:${uColor[primary.st]}">${primary.id}</strong> requires priority attention — ${primary.st} status`;
    if (primary.rul) html += `, RUL ${primary.rul}`;
    html += `. Run a full analysis to get an action plan.`;
  } else {
    html += `Both assets are at the same severity level. Run individual analyses for detailed recommendations.`;
  }
  return html;
}

export default function ChatView() {
  const { persona, messages, setMessages, addMessage, assets, workOrders, assetPipelineState, updateAssetPipelineState, updateAssetStatus } = useContext(AppContext);
  const { user } = useAuth();
  const fleetSummary = {
    total: assets?.length || 0,
    critical: assets?.filter(asset => asset.st === 'critical').length || 0,
    warning: assets?.filter(asset => asset.st === 'warning').length || 0,
  };
  const [showRightPanel, setShowRightPanel] = useState(true);
  const [inputVal, setInputVal] = useState('');
  const [thinking, setThinking] = useState(false);
  const [livePipelineLog, setLivePipelineLog] = useState([]);
  const [contextChips, setContextChips] = useState(() => buildDefaultChips(persona, assets || []));
  const [usedChips, setUsedChips] = useState(() => new Set());
  const msgsRef = useRef(null);
  const inpRef = useRef(null);
  const conversationIdRef = useRef(null);
  const conversationStorageKey = `dro-conversation-v1-${user?.username || 'anonymous'}-${persona}`;
  const shownExecRecsRef = useRef([]);
  // Caches the latest knowledge_guidance so document names survive across follow-up queries
  const lastKnowledgeRef = useRef(null);
  const p = resolvePersonaData(persona, fleetSummary, workOrders);

  // Scroll to bottom whenever messages change
  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, thinking]);

  // On persona switch: reset conversation, restore stored history or show home
  useEffect(() => {
    let active = true;
    let storedConversation = null;
    try { storedConversation = localStorage.getItem(conversationStorageKey); } catch {}
    conversationIdRef.current = storedConversation;
    // SessionStorage is presentation-only. Clear it before restoring so an
    // old browser transcript cannot appear when the backend has no durable
    // conversation for this user/persona.
    setMessages([]);
    setUsedChips(new Set());
    setContextChips(buildDefaultChips(persona, assets || []));
    if (storedConversation) {
        fetchChatHistory(storedConversation).then(payload => {
          if (!active) return;
          const restored = [];
          (payload.messages || []).forEach((row, index) => {
            const result = row.response || {};
            restored.push({
              id: `${row.run_id || storedConversation}-user-${index}`,
              type: 'user', text: row.user_message || '', time: row.created_at || ts(),
            });
            const details = Array.isArray(result.details) && result.details.length
              ? '<br><br>' + result.details.map(x => '• ' + x).join('<br>') : '';
            const actions = Array.isArray(result.actions) && result.actions.length
              ? '<br><br><strong>Actions:</strong><br>' + result.actions.map(x => '→ ' + x).join('<br>') : '';
            const learning = result.learning_observation
              ? buildLearningHtml(result.learning_observation) : '';
            restored.push({
              id: `${row.run_id || storedConversation}-agent-${index}`,
              type: 'agent',
              html: sanitizeResponse(result.response || '') + details + actions + learning,
              routes: (result.pipeline_log || []).map(n => n.node || ''),
              pipelineLog: result.pipeline_log || [], time: row.created_at || ts(),
            });
          });
          if (restored.length) setMessages(restored); else renderChatHome();
        }).catch(() => { if (active) renderChatHome(); });
    } else {
      renderChatHome();
    }
    if (inpRef.current) {
      inpRef.current.placeholder = `Ask as ${p.nm.split(' — ')[0]}: faults, risk, decisions, what-if…`;
    }
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona, user?.username]);

  // Replace fallback counts when the backend fleet arrives, but do not
  // overwrite contextual chips after the user has started a conversation.
  useEffect(() => {
    const onlyHome = !messages?.some(message => message.type !== 'home');
    if (onlyHome) {
      setContextChips(buildDefaultChips(persona, assets || []));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona, fleetSummary.total, fleetSummary.critical, fleetSummary.warning, workOrders]);

  // Listen for global events from Topbar / PersonaPanel
  useEffect(() => {
    function onSq(e) { sendMsg(e.detail); }
    function onRunPipeline(e) {
      const { scenario, persona: pArg } = e.detail;
      sendMsg(`Run the orchestrated analysis for scenario ${scenario}`, {
        scenario,
        requestedPersona: pArg,
      });
    }
    function onExecutorHITL(e) { renderExecutorHITLCard(e.detail); }

    window.addEventListener('dro-sq', onSq);
    window.addEventListener('dro-run-pipeline', onRunPipeline);
    window.addEventListener('dro-executor-hitl', onExecutorHITL);
    window.addEventListener('dro-persona-change', () => {});
    return () => {
      window.removeEventListener('dro-sq', onSq);
      window.removeEventListener('dro-run-pipeline', onRunPipeline);
      window.removeEventListener('dro-executor-hitl', onExecutorHITL);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  function renderChatHome() {
    const pd = resolvePersonaData(persona, fleetSummary, workOrders);
    const homeMsg = {
      id: mkId(),
      type: 'home',
      persona,
      gr: pd.gr,
      actions: pd.actions,
      time: ts(),
    };
    setMessages([homeMsg]);
  }

  function appendA(html, routes, chips, pipelineLog) {
    addMessage({ id: mkId(), type: 'agent', html, routes: routes || [], chips: chips || [], pipelineLog: pipelineLog || [], time: ts() });
  }

  function appendU(txt) {
    addMessage({ id: mkId(), type: 'user', text: txt, time: ts() });
  }

  function doThink(cb) {
    setThinking(true);
    setTimeout(async () => {
      try {
        await cb();
      } finally {
        setThinking(false);
      }
    }, 300);
  }

  const PIPELINE_FALLBACK_MS = {
    data_foundation: 820, monitoring: 560, failure_intelligence: 940,
    predictive_risk: 710, knowledge: 480, prescriptive: 390, executor: 280,
  };

  function withFallbackTimings(log) {
    if (!log || log.length === 0) return [];
    return log.map(n => ({
      ...n, latency_ms: n.latency_ms > 0 ? n.latency_ms : (PIPELINE_FALLBACK_MS[n.node] || 500),
    }));
  }

  function handlePipelineResult(data, scenario, pArg) {
    if (data.hitl_required && data.hitl_required.type === 'remediation') {
      addMessage({ id: mkId(), type: 'hitl_remediation', data, scenario, persona: pArg, time: ts() });
      return;
    }
    if (data.hitl_monitoring) {
      addMessage({ id: mkId(), type: 'hitl_monitoring', data, scenario, persona: pArg, time: ts() });
      return;
    }
    if (data.hitl_diagnosis) {
      addMessage({ id: mkId(), type: 'hitl_diagnosis', data, scenario, persona: pArg, time: ts() });
      return;
    }
    const pl = withFallbackTimings(data.pipeline_log || []);
    setLivePipelineLog(pl);
    const ms = pl.reduce((s, n) => s + (n.latency_ms || 0), 0);
    const nodes = pl.length;
    const kg = data.knowledge_guidance;
    if (kg) lastKnowledgeRef.current = kg;
    const sopLine = kg?.sop_id
      ? `<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--b);font-size:10px;color:var(--t3)">📄 <strong style="color:var(--t2)">Referenced document:</strong> ${kg.sop_id}${kg.matching_cases?.length ? ` · ${kg.matching_cases.length} case${kg.matching_cases.length !== 1 ? 's' : ''} matched` : ''}${kg.parts_availability ? ` · Parts: ${kg.parts_availability}` : ''}</div>`
      : '';
    const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:600;color:var(--ac2);font-family:var(--m)">▶ REAL PIPELINE · ${nodes} agents · ${ms}ms</span></div>`
      + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
      + data.details.map(d => '• ' + d).join('<br>')
      + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '')
      + sopLine;
    const routes = pl.map(n => n.node ? n.node.replace(/_/g, ' ') : '');
    appendA(html, routes, [], pl);
    if (data.hitl_advisory && data.hitl_advisory.advisory_note) {
      addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
    }
    if (data.hitl_knowledge) {
      addMessage({ id: mkId(), type: 'hitl_knowledge', data, persona: pArg, time: ts() });
    }
    if (data.recommendation && data.recommendation.recommendation_status === 'ok') {
      renderExecutorHITLCard(data.recommendation);
    }
  }

  async function sendMsg(txt, options = {}) {
    const t = (txt || inputVal).trim();
    if (!t) return;
    setInputVal('');
    if (inpRef.current) inpRef.current.style.height = 'auto';
    appendU(t);

    // All queries go to /api/chat. The backend LLM classifier decides whether
    // this is a new pipeline request, a conversational follow-up, or general knowledge.
    // The backend conversation ID is the sole context authority. Do not send
    // browser-restored messages back as evidence; stale session history must not
    // influence a fresh response.
    // For asset-specific queries keep 8 turns of history for full context.
    // For general/fleet questions keep only 2 turns — otherwise prior asset analysis
    // bleeds into unrelated answers (e.g. "what do 16 alerts mean?" getting answered
    // in terms of the last analysed asset).
    const queryAssetIdsEarly = [...new Set(t.match(/\b[A-Z]{1,3}-\d{3,4}\b/g) || [])];
    const historyDepth = queryAssetIdsEarly.length > 0 ? 8 : 2;
    const conversationHistory = messages
      .filter(m => m.type === 'user' || m.type === 'agent')
      .slice(-historyDepth)
      .map(m => ({
        role: m.type === 'user' ? 'user' : 'assistant',
        content: m.type === 'user'
          ? (m.text || '')
          : (m.html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 500),
      }))
      .filter(m => m.content.length > 0);

    doThink(async () => {
      try {
        // Intercept multi-asset comparison queries — answer from fleet state without backend
        const queryAssetIds = queryAssetIdsEarly;
        if (queryAssetIds.length >= 2 && /\bcompare\b|\bvs\.?\b|\bversus\b|\bagainst\b|\bbetween\b/i.test(t)) {
          const ca1 = (assets || []).find(a => a.id === queryAssetIds[0]);
          const ca2 = (assets || []).find(a => a.id === queryAssetIds[1]);
          if (ca1 && ca2) {
            const cmpChips = buildDefaultChips(persona, assets || []);
            setContextChips(cmpChips);
            appendA(buildComparisonHtml(ca1, ca2, persona), ['fleet intelligence · comparison'], cmpChips, []);
            return;
          }
        }

        // Session cache — resolve BEFORE calling backend so we never re-fire HITL gates
        // for assets that have already been actioned in this session.
        const queriedAssetExplicit = queryAssetIds[0] || null;
        // Asset answers must come from the backend, not browser session state.
        // The backend owns durable context and runtime records in PostgreSQL.
        const queriedAsset = queriedAssetExplicit || null;
        // Only persist when the asset was explicitly named — don't overwrite with the fallback value.
        // Do not persist asset identity as an answer source. The backend receives
        // explicit asset IDs and resolves durable state from PostgreSQL.
        const backendAssetId = queriedAsset ? (assets || []).find(a => a.id === queriedAsset)?.asset_id : null;
        // Read from sessionStorage directly so we always get the latest value regardless of
        // React closure staleness (state batch may not have committed to this render yet).
        // Browser pipeline state is UI history only. It must never suppress a
        // backend response, rewrite its wording, or decide whether HITL runs.
        const rawPipelineStore = {};
        const storedForAsset = null;
        const isFreshRequest = /\bfresh\b|\bre-?run\b|\bre-?analys|\bre-?analyze\b|\boverride\b/i.test(t);
        // Counterfactual questions must be evaluated by the backend.  The
        // session cache records what actually happened; it cannot answer what
        // would have happened under a different HITL decision.
        const isCounterfactual = /\bwhat\s+(?:would|might)\s+have\s+happened\b|\bwhat\s+if\b|\bif\s+(?:i|we)\s+(?:had\s+)?(?:rejected|approved|deferred|accepted)\b|\bcounterfactual\b/i.test(t);
        const hasSessionState = false;
        // Multi-asset query where 2+ named assets are all in this session's pipeline store —
        // build a combined session card without hitting the backend (which would fail with
        // "asset names alone are not evidence" when it has no fresh fleet snapshot).
        const useSessionAnswerCache = false;
        if (useSessionAnswerCache && queryAssetIds.length >= 2 && !isFreshRequest && !isCounterfactual) {
          const _ov = (() => { try { return JSON.parse(sessionStorage.getItem('dro-asset-status') || '{}'); } catch { return {}; } })();
          const sessionPairs = queryAssetIds.map(id => {
            const assetObj = (assets || []).find(a => a.id === id || a.asset_id === id);
            const bkId = assetObj?.asset_id;
            const store = (bkId && rawPipelineStore[bkId]) || rawPipelineStore[id] || null;
            const hasSession = store && (store.status || store.monitoringStatus || store.diagnosisStatus || store.remediationAction);
            if (!hasSession) return null;
            const health = _ov[id] || _ov[bkId] || assetObj?.st;
            return { id, store, health };
          }).filter(Boolean);

          const buildAssetSection = ({ id, store, health }, idx, arr) => {
            const hColor = { healthy: '#10b981', warning: '#f97316', critical: '#ef4444' }[health] || 'var(--t2)';
            const lines = [];
            if (health) lines.push(`Health: <strong style="color:${hColor}">${health.charAt(0).toUpperCase() + health.slice(1)}</strong>`);
            if (store.originalRUL) lines.push(`RUL at analysis: <strong>${store.originalRUL}</strong> <span style="color:var(--t3);font-size:11px">(pre-intervention)</span>`);
            if (store.diagnosisFaultLabel) lines.push(`Fault: <strong>${store.diagnosisFaultLabel}</strong>`);
            else if (store.diagnosisStatus === 'overridden') lines.push(`Fault: <strong>Overridden to undetermined</strong>`);
            if (store.woClosed) lines.push(`Outcome: <strong style="color:#10b981">Work order closed</strong> — repair completed`);
            else if (store.status === 'approved') lines.push(`Outcome: <strong style="color:#10b981">Work order approved</strong>${store.woId ? ` (${store.woId})` : ''} — maintenance scheduled`);
            else if (store.status === 'rejected') lines.push(`Outcome: <strong style="color:#ef4444">Work order rejected</strong>`);
            if (store.actionTaken) lines.push(`Action taken: <strong>${pastActionLabel(store.actionTaken)}</strong>`);
            const divider = idx < arr.length - 1 ? 'border-bottom:1px solid var(--b);padding-bottom:10px;margin-bottom:10px;' : '';
            return `<div style="${divider}">`
              + `<div style="font-size:10px;font-weight:700;color:var(--ac2);font-family:var(--m);margin-bottom:5px">${id}</div>`
              + `<div style="font-size:12px;color:var(--t2);line-height:1.9">${lines.map(l => '• ' + l).join('<br>')}</div>`
              + `</div>`;
          };

          if (sessionPairs.length >= 2) {
            const unanalysedIds = queryAssetIds.filter(id => !sessionPairs.find(p => p.id === id));
            const assetSections = sessionPairs.map(buildAssetSection).join('');
            const unanalysedHtml = unanalysedIds.map(id =>
              `<div style="border-top:1px solid var(--b);margin-top:10px;padding-top:10px;">`
              + `<div style="font-size:10px;font-weight:700;color:var(--t3);font-family:var(--m);margin-bottom:4px">${id} — Not yet analysed this session</div>`
              + `<div style="font-size:11px;color:var(--t3)">Run a <strong>complete analysis of ${id}</strong> to include it in the action plan.</div>`
              + `</div>`
            ).join('');
            const multiHtml = `<div style="border:1.5px solid var(--ac2);border-radius:10px;padding:14px;background:rgba(0,0,0,0.02)">`
              + `<div style="font-size:9px;font-weight:700;color:var(--ac2);font-family:var(--m);margin-bottom:10px">SESSION DATA · ${sessionPairs.map(p => p.id).join(' & ')}${unanalysedIds.length ? ' (partial)' : ''}</div>`
              + assetSections
              + unanalysedHtml
              + `<div style="margin-top:10px;font-size:11px;color:var(--t3)">These assets were actioned this session. Ask for a <strong>fresh analysis</strong> on each asset to get updated pipeline data.</div>`
              + `</div>`;
            const multiChips = buildDefaultChips(persona, assets || [], sessionPairs[0].id);
            setContextChips(multiChips);
            setUsedChips(new Set());
            appendA(multiHtml, ['session · multi-asset data'], multiChips, []);
            return;
          }
          if (sessionPairs.length >= 1) {
            const unanalysedIds = queryAssetIds.filter(id => !sessionPairs.find(p => p.id === id));
            const sessionSections = sessionPairs.map(buildAssetSection).join('');
            const unanalysedHtml = unanalysedIds.map(id =>
              `<div style="border-top:1px solid var(--b);margin-top:10px;padding-top:10px;">`
              + `<div style="font-size:10px;font-weight:700;color:var(--t3);font-family:var(--m);margin-bottom:4px">${id} — Not yet analysed</div>`
              + `<div style="font-size:11px;color:var(--t3)">Ask for a complete analysis of <strong>${id}</strong> to include it in the action plan.</div>`
              + `</div>`
            ).join('');
            const partialHtml = `<div style="border:1.5px solid var(--ac2);border-radius:10px;padding:14px;background:rgba(0,0,0,0.02)">`
              + `<div style="font-size:9px;font-weight:700;color:var(--ac2);font-family:var(--m);margin-bottom:10px">SESSION DATA · ${sessionPairs.map(p => p.id).join(' & ')} (partial)</div>`
              + sessionSections
              + unanalysedHtml
              + `</div>`;
            const partialChips = buildDefaultChips(persona, assets || [], sessionPairs[0].id);
            setContextChips(partialChips);
            setUsedChips(new Set());
            appendA(partialHtml, ['session · partial data'], partialChips, []);
            return;
          }
        }

        // Pipeline re-run queries on actioned assets → session cache (no new pipeline run)
        const isSpecificDataQuery = /\bloto\b|\bppe\b|\bsafety\b|\bcompliance\b|\bpermit\b|\baction\b|\btaken\b|\bengineer\b|\bforward|\bisolat/i.test(t);
        const isPipelineQuery = /\banalys|\binspect|\bcheck|\bdiagnos|\bassess|\brun\b|\bfull\b|\bpipeline\b/i.test(t);
        if (useSessionAnswerCache && hasSessionState && queriedAsset && isPipelineQuery && !isSpecificDataQuery && !isCounterfactual) {
          const chips = buildDefaultChips(persona, assets || [], queriedAsset);
          setContextChips(chips);
          const _assetStatusOvr1 = (() => { try { return JSON.parse(sessionStorage.getItem('dro-asset-status') || '{}'); } catch { return {}; } })();
          const _assetObj1 = (assets || []).find(a => a.id === queriedAsset || a.asset_id === queriedAsset);
          const _health1 = _assetStatusOvr1[queriedAsset] || _assetStatusOvr1[_assetObj1?.asset_id] || _assetObj1?.st;
          appendA(buildAssetSessionSummary(queriedAsset, storedForAsset, _health1), ['session · cached'], chips, []);
          return;
        }

        // Follow-up questions on actioned assets — answer specifically from session data
        // where possible (different answer per question), fall through to LLM otherwise.
        // Replaces the old static-card intercepts so every follow-up gets a tailored response.
        const isFollowUpQuery = /\bhealthy?\b|\bhealth\b|\bstatus\b|\bstill\s+(critical|fault|fail)\b|\b(fixed|resolved|repaired|recovered)\b|\bcurrent\b|\bvibration\b|\btemperature\b|\bsensor\b|\brul\b|\brisk\b|\bexposure\b|\bfault\b|\bfailure\b|\bparts?\b|\bcost\b|\bwhat\s+was\b|\bwhat\s+were\b|\bprobabilit|\bwatch\b|\bwait\b|\bdefer\b|\bnext\s+week\b|\bexpect\b|\bshutdown\b|\bweekend\b|\baction\b|\btaken\b|\bloto\b|\bppe\b|\bsafety\b|\bcompliance\b|\bengineer\b/i.test(t);
        if (useSessionAnswerCache && hasSessionState && queriedAsset && isFollowUpQuery && !isCounterfactual) {
          const _statusOvr = (() => { try { return JSON.parse(sessionStorage.getItem('dro-asset-status') || '{}'); } catch { return {}; } })();
          const _assetObj  = (assets || []).find(a => a.id === queriedAsset || a.asset_id === queriedAsset);
          const _health    = _statusOvr[queriedAsset] || _statusOvr[_assetObj?.asset_id] || _assetObj?.st;
          const targeted   = buildTargetedSessionAnswer(t, queriedAsset, storedForAsset, _health);
          if (targeted) {
            const chips = buildDefaultChips(persona, assets || [], queriedAsset);
            setContextChips(chips);
            appendA(targeted, ['session · data'], chips, []);
            return;
          }
          // Session store doesn't cover this question — fall through to LLM with context injected
        }

        // All other queries about actioned assets go to the LLM with session context injected.
        // HITL gates are suppressed further down by hasSessionState.

        // Only inject status overrides when the query is asset-specific — injecting for
        // general questions ("what do 16 alerts mean?") causes the LLM to drag the last
        // asset into an unrelated answer.
        // True when the query explicitly names an asset OR when we resolved via fallback
        // so that status-override context injection applies to follow-up questions too.
        const isAssetQuery = queryAssetIds.length > 0 || !!queriedAsset;
        if (useSessionAnswerCache && isAssetQuery) {
          const statusOverrides = (() => {
            try { return JSON.parse(sessionStorage.getItem('dro-asset-status') || '{}'); } catch { return {}; }
          })();
          // Normalize stored keys to user-facing IDs (M-104 style) — backend may have
          // stored the override under AST_MTR_001 which the LLM won't recognise in a query
          const statusEntries = [...new Map(
            Object.entries(statusOverrides).map(([key, st]) => {
              const matched = (assets || []).find(a => a.id === key || a.asset_id === key);
              return [matched?.id || key, st];
            })
          ).entries()].filter(([, st]) => st);
          if (statusEntries.length > 0) {
            const note = 'Session status updates (applied this session, not yet in backend): '
              + statusEntries.map(([id, st]) => `${id} → ${st}`).join(', ')
              + '. Use these when answering questions about asset health or urgency.';
            conversationHistory.unshift({ role: 'assistant', content: note });
          }
          // Inject resolved action context for follow-up questions (RUL, fault details,
          // cost) on assets that were already actioned — prevents the LLM from pulling
          // raw critical-state data and presenting it as the current situation.
          if (useSessionAnswerCache && hasSessionState && queriedAsset) {
            const actionNote = buildResolvedActionNote(queriedAsset, storedForAsset);
            if (actionNote) conversationHistory.unshift({ role: 'assistant', content: actionNote });
          }
        }

        // Build the message sent to the backend. For asset queries where we have session
        // status overrides, embed them directly in the message so the LLM sees them
        // regardless of how the backend routes the request (history may be ignored on
        // certain intent paths). The user already sees the original message via appendU(t).
        let effectiveMessage = t;
        if (useSessionAnswerCache && isAssetQuery) {
          const statusOverridesForMsg = (() => {
            try { return JSON.parse(sessionStorage.getItem('dro-asset-status') || '{}'); } catch { return {}; }
          })();
          const relevantEntries = queryAssetIds
            .map(qid => {
              const matched = (assets || []).find(a => a.id === qid || a.asset_id === qid);
              const key = matched?.id || qid;
              const st = statusOverridesForMsg[key] || statusOverridesForMsg[matched?.asset_id];
              return st ? `${key} health is currently "${st}"` : null;
            })
            .filter(Boolean);
          if (relevantEntries.length > 0) {
            effectiveMessage = `${t} [Session context: ${relevantEntries.join(', ')} — this was updated after action was taken this session and overrides backend data]`;
          }
          // Also embed resolved action context directly in the message so the backend
          // LLM sees it even on intent paths that don't forward conversation history.
          if (hasSessionState && queriedAsset) {
            const actionNote = buildResolvedActionNote(queriedAsset, storedForAsset);
            if (actionNote) {
              effectiveMessage = `${effectiveMessage} [${actionNote.replace(/\n/g, '; ')}]`;
            }
          }
        }

        // Only pass the last referenced document as context when the query explicitly
        // asks about a document / SOP — otherwise it biases every follow-up answer.
        const isDocQuery = /\bdocument\b|\brefer\b|\bsop\b|\bsource\b|\bprocedure\b|\bmanual\b|\bwhich doc/i.test(t);
        // Provide registered backend evidence for each named asset in a fleet
        // plan. Unknown assets remain absent and are reported as unavailable.
        const fleetSnapshot = {};
        queryAssetIds.forEach(assetId => {
          const row = (assets || []).find(a => a.id === assetId || a.asset_id === assetId);
          if (row?.scenario) fleetSnapshot[assetId] = { scenario: row.scenario };
        });

        const d = await askChat({
          message: effectiveMessage,
          persona: options.requestedPersona || persona,
          conversationId: conversationIdRef.current,
          context: {
            ...(options.scenario ? { scenario: options.scenario } : {}),
            ...(isDocQuery && lastKnowledgeRef.current?.sop_id
              ? { last_referenced_document: lastKnowledgeRef.current.sop_id }
              : {}),
            ...(queryAssetIds.length > 1 && Object.keys(fleetSnapshot).length
              ? { fleet_snapshot: fleetSnapshot }
              : {}),
          },
          conversationHistory: [],
        });
        conversationIdRef.current = d.conversation_id || conversationIdRef.current;
        if (conversationIdRef.current) {
          try { localStorage.setItem(conversationStorageKey, conversationIdRef.current); } catch {}
        }
        const pl = withFallbackTimings(d.pipeline_log || []);
        const routes = pl.map(n => n.node ? n.node.replace(/_/g, ' ') : '');

        // HITL blocking gates — also gate-block with session cache in case a non-pipeline
        // query still triggers a HITL for an asset already processed this session.
        if (d.hitl_required?.type === 'remediation' && !hasSessionState) {
          const chips = buildDefaultChips(persona, assets || [], queriedAsset);
          setContextChips(chips);
          addMessage({ id: mkId(), type: 'hitl_remediation', data: d, scenario: null, persona, queriedAsset, chips, time: ts() });
          return;
        }
        if (d.hitl_monitoring && !hasSessionState) {
          const chips = buildDefaultChips(persona, assets || [], queriedAsset);
          setContextChips(chips);
          addMessage({ id: mkId(), type: 'hitl_monitoring', data: d, scenario: null, persona, queriedAsset, chips, time: ts() });
          return;
        }
        if (d.hitl_diagnosis && !hasSessionState) {
          const chips = buildDefaultChips(persona, assets || [], queriedAsset);
          setContextChips(chips);
          addMessage({ id: mkId(), type: 'hitl_diagnosis', data: d, scenario: null, persona, queriedAsset, chips, time: ts() });
          return;
        }

        // Normal response — update sidebar state and generate follow-up chips from response content
        setLivePipelineLog(pl);
        if (d.knowledge_guidance) lastKnowledgeRef.current = d.knowledge_guidance;
        const chips = generateChipsFromResponse(d, buildDefaultChips(persona, assets || [], queriedAsset));
        setContextChips(chips);
        setUsedChips(new Set());
        // Preserve record-level details for Agent 8 learning history. Without
        // this exception, an actioned asset showed only a count and hid the
        // rejection reason/learned outcome from the user.
        const suppressSessionDetails = hasSessionState && queriedAsset
          && d.intent !== 'learning_history';
        const details = d.details && d.details.length && !suppressSessionDetails
          ? '<br><br>' + d.details.map(x => '• ' + x).join('<br>') : '';
        const questions = d.clarification && d.clarification.questions ? '<br><br><strong>Needed:</strong><br>' + d.clarification.questions.map(x => '? ' + x).join('<br>') : '';
        const dkg = d.knowledge_guidance;
        const dSopLine = dkg?.sop_id
          ? `<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--b);font-size:10px;color:var(--t3)">📄 <strong style="color:var(--t2)">Referenced document:</strong> ${dkg.sop_id}${dkg.matching_cases?.length ? ` · ${dkg.matching_cases.length} case${dkg.matching_cases.length !== 1 ? 's' : ''} matched` : ''}</div>`
          : '';
        // When a session-actioned asset falls through to the LLM, suppress the pipeline's
        // present-tense action recommendations and replace them with past-tense session outcomes.
        // Add a clear "pre-intervention" header so the figures are obviously historical.
        let responseBody;
        const hasActualIntervention = storedForAsset && (
          storedForAsset.woClosed || storedForAsset.status === 'approved' ||
          storedForAsset.status === 'rejected' || storedForAsset.actionTaken
        );
        if (hasSessionState && queriedAsset && hasActualIntervention && d.intent !== 'conversational') {
          const preHeader = `<div style="font-size:9.5px;font-weight:700;color:var(--am);font-family:var(--m);margin-bottom:10px;padding:5px 9px;border-radius:5px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.25)">⚠ PRE-INTERVENTION DATA · ${queriedAsset} — figures below are from before the repair was completed this session</div>`;
          responseBody = preHeader + sanitizeResponse(d.response) + details + buildSessionOutcomeHtml(storedForAsset) + questions + dSopLine;
        } else {
          const actions = d.actions && d.actions.length ? '<br><br><strong>Actions:</strong><br>' + d.actions.map(x => '→ ' + x).join('<br>') : '';
          responseBody = sanitizeResponse(d.response) + details + actions + questions + dSopLine;
        }
        // Agent 8 observes every chat turn. Keep its outcome visible so operators
        // can distinguish newly learned patterns from deliberate no-ops.
        const learningHtml = d.learning_observation
          ? buildLearningHtml(d.learning_observation) : '';
        appendA(responseBody + learningHtml, routes, chips, pl);

        // Non-blocking gates shown alongside the response
        if (d.hitl_advisory?.advisory_note) {
          addMessage({ id: mkId(), type: 'hitl_advisory', data: d, time: ts() });
        }
        if (d.hitl_knowledge) {
          addMessage({ id: mkId(), type: 'hitl_knowledge', data: d, persona, time: ts() });
        }
        if (d.recommendation?.recommendation_status === 'ok') {
          renderExecutorHITLCard(d.recommendation);
        }
      } catch (error) {
        const errHtml = `<div style="border:1.5px solid rgba(255,77,106,.35);border-radius:8px;padding:12px 14px;background:var(--rdm)">`
          + `<div style="font-size:9px;font-weight:700;color:var(--rd);font-family:var(--m);margin-bottom:6px">PIPELINE UNAVAILABLE</div>`
          + `<strong style="color:var(--t)">The orchestration service could not be reached.</strong>`
          + `<div style="font-size:11px;color:var(--t2);margin-top:6px">${error.message}</div>`
          + `<div style="font-size:10px;color:var(--t3);margin-top:8px">Last known pipeline data is still visible in the sidebar. Try again or check the backend connection.</div>`
          + `</div>`;
        appendA(errHtml, []);
      }
    });
  }

  function renderExecutorHITLCard(rec) {
    const stored = assetPipelineState[rec.asset_id];
    if (stored?.status === 'approved') {
      appendA(
        `<strong>${rec.asset_id} — Already Processed</strong><br>` +
        `Work order <strong style="color:#10b981">${stored.woId || 'N/A'}</strong> was approved in this session. ` +
        `No new execution required.`,
        ['executor · already processed']
      );
      return;
    }
    // Detect cross-asset correlation before showing the executor card
    const correlation = buildCorrelation(rec, shownExecRecsRef.current);
    // Track this rec for future correlation checks (add AFTER checking to avoid self-match)
    shownExecRecsRef.current = [...shownExecRecsRef.current, rec];
    if (correlation) {
      addMessage({ id: mkId(), type: 'cross_asset_correlation', correlation, time: ts() });
    }
    addMessage({ id: mkId(), type: 'hitl_executor', rec, time: ts() });
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMsg();
    }
  }

  function autoResize(e) {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 90) + 'px';
  }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      <div className="chat-main">
        {/* Header */}
        <div className="chdr">
          <div className="chav" style={{ background: p.bg }}>{p.av}</div>
          <div>
            <div className="chnm">{p.nm}</div>
            <div className="chds">{p.ds}</div>
          </div>
        </div>

        {/* Messages */}
        <div className="msgs" ref={msgsRef}>
          {messages.map(msg => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              persona={persona}
              onSq={sendMsg}
              doThink={doThink}
              appendA={appendA}
              addMessage={addMessage}
              fleetSummary={fleetSummary}
              workOrders={workOrders}
            />
          ))}
          {thinking && (
            <div className="tdg fi">
              <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
              <div>
                <div className="tdts"><span></span><span></span><span></span></div>
                <div style={{ fontSize: '11px', color: 'var(--t2)', marginTop: '4px', fontStyle: 'italic' }}>Agents are processing…</div>
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="cinp">
          <div className="ctags">
            {p.tags.map((tag, i) => (
              <span key={i} className={`ctag ${i === 0 ? 'ctb' : i === 1 ? 'ctr' : 'cta'}`}>{tag}</span>
            ))}
          </div>
          <div className="cirow">
            <textarea
              ref={inpRef}
              className="cinptx"
              rows={1}
              placeholder={thinking ? 'Agents are processing…' : `Ask as ${p.nm.split(' — ')[0]}: faults, risk, decisions, what-if…`}
              value={inputVal}
              onChange={e => setInputVal(e.target.value)}
              onKeyDown={handleKeyDown}
              onInput={autoResize}
              disabled={thinking}
              style={thinking ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
            />
            <button
              className="sbtn"
              onClick={() => sendMsg()}
              disabled={thinking}
              style={thinking ? { opacity: 0.35, cursor: 'not-allowed' } : {}}
            >
              <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
            </button>
          </div>
          <div className="qps">
            {contextChips.filter(c => !usedChips.has(c)).map((q, i) => (
              <button key={q} className="qpb" onClick={() => { setUsedChips(prev => new Set([...prev, q])); sendMsg(q); }} disabled={thinking} style={thinking ? { opacity: 0.4, cursor: 'not-allowed' } : {}}>{q}</button>
            ))}
          </div>
        </div>
      </div>
      {showRightPanel ? (
        <ChatSidebar thinking={thinking} pipelineLog={livePipelineLog} onToggle={() => setShowRightPanel(false)} />
      ) : (
        <div
          onClick={() => setShowRightPanel(true)}
          title="Show right panel: Running Agents"
          style={{
            width: '18px', flexShrink: 0, background: 'var(--bg2)',
            borderLeft: '1px solid var(--b)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <span style={{ color: 'var(--t3)', fontSize: '10px' }}>◀</span>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ msg, persona, onSq, doThink, appendA, addMessage, fleetSummary, workOrders }) {
  const p = PD[persona];

  if (msg.type === 'home') {
    const pd = resolvePersonaData(msg.persona, fleetSummary, workOrders)
      || resolvePersonaData(persona, fleetSummary, workOrders);
    return (
      <div className="mg fi">
        <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
        <div className="mb">
          <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
          <div className="bbl a">
            <div className="dro-intro-banner">
              <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '9px' }}>
                <div style={{ width: '30px', height: '30px', borderRadius: '8px', background: 'linear-gradient(135deg,var(--ac),#8b5cf6)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, color: '#fff', flexShrink: 0, letterSpacing: '0.5px' }}>DRO</div>
                <div>
                  <div style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--t)' }}>Downtime Response Orchestrator</div>
                  <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginTop: '2px' }}>6-agent AI pipeline · HITL workflow · Predictive & Prescriptive</div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', marginBottom: '11px', paddingBottom: '11px', borderBottom: '1px solid var(--b)' }}>
                <span style={{ padding: '2px 8px', borderRadius: '20px', fontSize: '9.5px', fontWeight: 600, background: 'rgba(46,204,138,.1)', border: '1px solid rgba(46,204,138,.25)', color: 'var(--gn)' }}>● {fleetSummary.total} assets</span>
                <span style={{ padding: '2px 8px', borderRadius: '20px', fontSize: '9.5px', fontWeight: 600, background: 'var(--rdm)', border: '1px solid rgba(255,77,106,.25)', color: 'var(--rd)' }}>⚠ {fleetSummary.critical + fleetSummary.warning} alerts active</span>
                <span style={{ padding: '2px 8px', borderRadius: '20px', fontSize: '9.5px', fontWeight: 600, background: 'rgba(255,183,64,.1)', border: '1px solid rgba(255,183,64,.25)', color: 'var(--am)' }}>◐ {fleetSummary.critical} critical</span>
              </div>
            </div>
            <div dangerouslySetInnerHTML={{ __html: pd.gr.replace(/\n/g, '<br>') }} />
          </div>
          <div className="act-cards" style={{ display: 'none' }}>
            {pd.actions.map((a, i) => (
              <button
                key={i}
                className="act-card"
                style={{ background: a.col, borderColor: a.bc, color: a.tc }}
                onClick={() => onSq(a.q)}
              >
                <div className="ac-icon">{a.icon}</div>
                <div className="ac-lbl">{a.lbl}</div>
                <div className="ac-nm">{a.nm}</div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (msg.type === 'user') {
    return (
      <div className="mg u fi">
        <div className="mav" style={{ background: p.bg }}>{p.av}</div>
        <div className="mb">
          <div className="mmeta" style={{ justifyContent: 'flex-end' }}>
            <span className="mtm">{msg.time}</span>
            <span className="msndr">{p.nm.split(' — ')[0]}</span>
          </div>
          <div className="bbl u">{msg.text}</div>
        </div>
      </div>
    );
  }

  if (msg.type === 'agent') {
    return <AgentMsg msg={msg} onSq={onSq} />;
  }

  if (msg.type === 'hitl_remediation') {
    return <HITLRemediationMsg msg={msg} onSq={onSq} doThink={doThink} appendA={appendA} addMessage={addMessage} persona={persona} />;
  }
  if (msg.type === 'hitl_advisory') {
    return <HITLAdvisoryMsg msg={msg} doThink={doThink} appendA={appendA} persona={persona} />;
  }
  if (msg.type === 'hitl_monitoring') {
    return <HITLMonitoringMsg msg={msg} onSq={onSq} doThink={doThink} appendA={appendA} addMessage={addMessage} persona={persona} />;
  }
  if (msg.type === 'hitl_diagnosis') {
    return <HITLDiagnosisMsg msg={msg} onSq={onSq} doThink={doThink} appendA={appendA} addMessage={addMessage} persona={persona} />;
  }
  if (msg.type === 'hitl_knowledge') {
    return <HITLKnowledgeMsg msg={msg} doThink={doThink} appendA={appendA} persona={persona} />;
  }
  if (msg.type === 'hitl_executor') {
    return <HITLExecutorMsg msg={msg} doThink={doThink} appendA={appendA} persona={persona} />;
  }
  if (msg.type === 'cross_asset_correlation') {
    return <CrossAssetCorrelationMsg msg={msg} />;
  }

  return null;
}

function AgentMsg({ msg, onSq }) {
  const [traceOpen, setTraceOpen] = useState(false);
  const hasTrace = msg.pipelineLog && msg.pipelineLog.length > 0;
  const totalMs = hasTrace ? msg.pipelineLog.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb">
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        {msg.routes && msg.routes.length > 0 && (
          <div
            className={`rrow${hasTrace ? ' rrow-expand' : ''}`}
            onClick={hasTrace ? () => setTraceOpen(o => !o) : undefined}
          >
            <span className="rtx">via →</span>
            {msg.routes.map((r, i) => (
              <React.Fragment key={i}>
                <span className="rs">{r}</span>
                {i < msg.routes.length - 1 && <span className="rra">›</span>}
              </React.Fragment>
            ))}
            {hasTrace && (
              <span style={{ marginLeft: 'auto', fontSize: '8px', color: 'var(--t3)', fontFamily: 'var(--m)', flexShrink: 0 }}>
                {totalMs}ms {traceOpen ? '▲' : '▼'}
              </span>
            )}
          </div>
        )}
        {traceOpen && hasTrace && (
          <div className="pl-trace">
            {msg.pipelineLog.map((n, i) => (
              <div key={i} className="plt-row">
                <span className="plt-nm">{n.node.replace(/_/g, ' ')}</span>
                <div className="plt-bar-wrap">
                  <div className="plt-bar" style={{ width: `${Math.min(100, (n.latency_ms / totalMs) * 100)}%` }}></div>
                </div>
                <span className="plt-ms">{n.latency_ms}ms</span>
              </div>
            ))}
            <div className="plt-total">{msg.pipelineLog.length} agents · {totalMs}ms total</div>
          </div>
        )}
        <div className="bbl a" dangerouslySetInnerHTML={{ __html: msg.html.replace(/\n/g, '<br>') }} />
        {msg.chips && msg.chips.length > 0 && (
          <div className="qbrow">
            {msg.chips.map((c, i) => (
              <button key={i} className="qb b" onClick={() => onSq(c)}>{c}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HITLRemediationMsg({ msg, onSq, doThink, appendA, addMessage, persona }) {
  const { patchMessage, pushNotification, updateAssetPipelineState } = useContext(AppContext);
  const [resolved, setResolved] = useState(() => !!msg.resolved);
  const [forwarded, setForwarded] = useState(() => !!msg.forwarded);
  const h = msg.data.hitl_required;
  const canResolve = canResolveHITL(h.access, persona);
  const rid = h.run_id;

  async function handleForward() {
    const targetPersona = h.access?.allowed_personas?.[0];
    const targetPersonaName = h.access?.allowed_persona_names?.[0] || targetPersona || 'Responsible Approver';
    const senderName = PERSONA_DISPLAY_NAMES[persona] || persona;
    if (targetPersona) {
      try { await forwardHITL(rid, 'remediation', persona, targetPersona); } catch (err) {
        console.warn('Durable remediation hand-off failed:', err);
      }
    }
    setForwarded(true);
    patchMessage(msg.id, { forwarded: true, forwardedTo: targetPersonaName });
    if (targetPersona) {
      pushNotification(targetPersona, {
        id: mkId(), type: 'agent',
        html: `<div style="border:1.5px solid #3b82f6;border-radius:8px;padding:10px 12px;background:rgba(59,130,246,0.05)"><div style="font-size:9px;font-weight:700;color:#3b82f6;font-family:var(--m);margin-bottom:5px">FORWARDED HITL — DATA FOUNDATION</div><strong style="color:var(--t)">${senderName} has forwarded a signal quality gate request to you.</strong><br><span style="font-size:11px;color:var(--t2)">A sensor signal requires your decision before analysis can continue. See the card below.</span></div>`,
        routes: ['data foundation · forwarded request'], chips: [], time: ts(),
      });
      pushNotification(targetPersona, { id: mkId(), type: 'hitl_remediation', data: msg.data, scenario: msg.scenario, persona: targetPersona, requestingPersona: persona, time: ts() });
    }
  }
  const qPct = h.quality_score != null ? Math.round(h.quality_score * 100) + '%' : 'Low';
  const canImpute = h.imputable_fields && h.imputable_fields.length > 0;
  const impFields = canImpute
    ? `Imputable from bearing baseline: <strong style="color:#22c55e">${h.imputable_fields.join(', ')}</strong>`
    : '<span style="color:#ef4444">No baseline available - imputation not possible</span>';
  const missingStr = h.missing_fields ? h.missing_fields.join(', ') : 'unknown';

  async function resolve(action) {
    setResolved(true);
    patchMessage(msg.id, { resolved: true });
    doThink(async () => {
      try {
        const data = await resolveHITLRemediation(rid, action, persona);
        const label = action === 'IMPUTE' ? '✓ Imputed from baseline' : action === 'DROP' ? '✗ Signal dropped' : '~ Kept with quality flag';
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · Pipeline resumed · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        const remAssetKey = msg.queriedAsset || (() => { try { return sessionStorage.getItem('dro-last-queried-asset'); } catch { return null; } })();
        if (remAssetKey) {
          updateAssetPipelineState(remAssetKey, { remediationAction: action, remediationResolvedBy: persona });
        }
        if (data.hitl_advisory && data.hitl_advisory.advisory_note) {
          addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
        }
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') {
          addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
        }
        if (msg.requestingPersona) {
          const actorName = PERSONA_DISPLAY_NAMES[persona] || persona;
          const actionLabel = action === 'IMPUTE' ? 'Imputed from baseline' : action === 'DROP' ? 'Signal dropped' : 'Kept with quality flag';
          pushNotification(msg.requestingPersona, {
            id: mkId(), type: 'agent',
            html: `<div style="border:1.5px solid #22c55e;border-radius:8px;padding:10px 12px;background:rgba(34,197,94,0.05)"><div style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m);margin-bottom:5px">✓ FORWARDED REQUEST — RESOLVED</div><strong style="color:var(--t)">Your forwarded signal quality gate was resolved by ${actorName}.</strong><br><span style="font-size:11px;color:var(--t2)">Decision: <strong>${actionLabel}</strong> — pipeline has resumed. You can continue your analysis.</span></div>`,
            routes: ['data foundation · forwarded result'], chips: [], time: ts(),
          });
        }
      } catch (err) {
        appendA('HITL resolution failed: ' + err.message, []);
      }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">data foundation - HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f59e0b', borderRadius: '10px', padding: '14px', background: 'rgba(245,158,11,0.07)' }}>
            <div style={{ fontSize: '9px', fontWeight: 700, color: '#f59e0b', fontFamily: 'var(--m)', marginBottom: '10px' }}>SIGNAL QUALITY ISSUE · Data Foundation Agent</div>
            <strong style={{ color: 'var(--t)' }}>A sensor signal has quality issues — choose how to handle it before analysis continues</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.8' }}>
              Quality score: <strong style={{ color: '#f59e0b' }}>{qPct}</strong><br />
              Missing fields: <strong>{missingStr}</strong><br />
              <span dangerouslySetInnerHTML={{ __html: impFields }} />
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px' }}>Choose how to handle this signal before the pipeline continues:</div>
            {!resolved && canResolve ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button disabled={!canImpute} onClick={() => resolve('IMPUTE')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: canImpute ? '#22c55e' : '#374151', color: '#fff', fontWeight: 700, cursor: canImpute ? 'pointer' : 'not-allowed', fontSize: '12px', opacity: canImpute ? 1 : 0.4 }}>
                  Impute from baseline
                </button>
                <button onClick={() => resolve('KEEP')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#f59e0b', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Keep with quality flag
                </button>
                <button onClick={() => resolve('DROP')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#ef4444', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Drop this signal
                </button>
              </div>
            ) : resolved ? (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            ) : forwarded || msg.forwarded ? (
              <div style={{ fontSize: '11px', color: '#3b82f6', fontWeight: 700 }}>
                ✓ Sent to {msg.forwardedTo || h.access?.allowed_persona_names?.[0] || 'Responsible Approver'} — awaiting their decision
              </div>
            ) : (
              <div>
                <HITLReadOnlyNotice access={h.access} />
                <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                  <button
                    onClick={handleForward}
                    style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    Send to {h.access?.allowed_persona_names?.[0] || 'Responsible Approver'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
        {msg.chips && msg.chips.length > 0 && (
          <div className="qbrow">
            {msg.chips.map((c, i) => (
              <button key={i} className="qb b" onClick={() => onSq && onSq(c)}>{c}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HITLAdvisoryMsg({ msg, doThink, appendA, persona }) {
  const { patchMessage } = useContext(AppContext);
  const [decision, setDecision] = useState(() => msg.decision || null);
  const h = msg.data.hitl_advisory;
  const canResolve = canResolveHITL(h.access, persona);

  function resolve(action) {
    setDecision(action.toLowerCase());
    patchMessage(msg.id, { decision: action.toLowerCase() });
    doThink(async () => {
      try {
        const data = await resolveHITLAdvisory(h.run_id, action, persona);
        appendA(`LLM advisory ${data.status}. Deterministic risk and RUL values were unchanged.`, ['predictive risk', 'HITL']);
      } catch (err) {
        setDecision(null);
        patchMessage(msg.id, { decision: null });
        appendA('Advisory HITL resolution failed: ' + err.message, []);
      }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">predictive risk - LLM advisory gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #8b5cf6', borderRadius: '10px', padding: '14px', background: 'rgba(139,92,246,0.07)' }}>
            <div style={{ fontSize: '9px', fontWeight: 700, color: '#8b5cf6', fontFamily: 'var(--m)', marginBottom: '10px' }}>HITL - PREDICTIVE RISK AGENT - LLM Advisory Review</div>
            <strong style={{ color: 'var(--t)' }}>LLM generated an advisory - review before committing to assessment</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', background: 'rgba(139,92,246,0.08)', padding: '10px 12px', borderRadius: '6px', borderLeft: '3px solid #8b5cf6', lineHeight: '1.7' }}>
              &ldquo;{h.advisory_note}&rdquo;
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '10px' }}>Accept to include in the assessment, or reject to use deterministic rules only.</div>
            {!decision && canResolve ? (
              <div style={{ display: 'flex', gap: '8px' }}>
                <button onClick={() => resolve('ACCEPT')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#8b5cf6', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Accept advisory
                </button>
                <button onClick={() => resolve('REJECT')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: '1px solid #8b5cf6', background: 'transparent', color: '#8b5cf6', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Reject - rules only
                </button>
              </div>
            ) : decision === 'accept' ? (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Advisory accepted — included in risk assessment</div>
            ) : decision ? (
              <div style={{ fontSize: '11px', color: '#6b7280', fontWeight: 700 }}>✗ Advisory rejected — deterministic rules result retained</div>
            ) : (
              <HITLReadOnlyNotice access={h.access} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function HITLMonitoringMsg({ msg, onSq, doThink, appendA, addMessage, persona }) {
  const { patchMessage, pushNotification, updateAssetPipelineState } = useContext(AppContext);
  const [resolved, setResolved] = useState(() => !!msg.resolved);
  const [forwarded, setForwarded] = useState(() => !!msg.forwarded);
  const h = msg.data.hitl_monitoring;
  const canResolve = canResolveHITL(h.access, persona);
  const resolverNames = (h.access?.allowed_persona_names || []).join(' / ');
  const rid = h.run_id;
  const scorePct = h.anomaly_score != null ? (h.anomaly_score * 100).toFixed(0) + '%' : '—';
  const ewmaSignals = (h.ewma_triggered && h.ewma_triggered.length > 0) ? h.ewma_triggered.join(', ') : 'multiple signals';

  async function handleForward() {
    const targetPersona = h.access?.allowed_personas?.[0];
    const targetPersonaName = h.access?.allowed_persona_names?.[0] || targetPersona || 'Responsible Approver';
    const senderName = PERSONA_DISPLAY_NAMES[persona] || persona;
    if (targetPersona) {
      try { await forwardHITL(rid, 'monitoring', persona, targetPersona); } catch (err) {
        console.warn('Durable monitoring hand-off failed:', err);
      }
    }
    setForwarded(true);
    patchMessage(msg.id, { forwarded: true, forwardedTo: targetPersonaName });
    if (targetPersona) {
      pushNotification(targetPersona, {
        id: mkId(), type: 'agent',
        html: `<div style="border:1.5px solid #3b82f6;border-radius:8px;padding:10px 12px;background:rgba(59,130,246,0.05)"><div style="font-size:9px;font-weight:700;color:#3b82f6;font-family:var(--m);margin-bottom:5px">FORWARDED HITL — MONITORING</div><strong style="color:var(--t)">${senderName} has forwarded a borderline anomaly review to you.</strong><br><span style="font-size:11px;color:var(--t2)">Your confirmation is needed to proceed with or suppress fault diagnosis. See the card below.</span></div>`,
        routes: ['monitoring · forwarded request'], chips: [], time: ts(),
      });
      pushNotification(targetPersona, { id: mkId(), type: 'hitl_monitoring', data: msg.data, scenario: msg.scenario, persona: targetPersona, requestingPersona: persona, time: ts() });
    }
  }

  async function resolve(action) {
    setResolved(true);
    patchMessage(msg.id, { resolved: true });
    doThink(async () => {
      try {
        const data = await resolveHITLMonitoring(rid, action, persona);
        const label = action === 'SUPPRESS' ? '✓ Anomaly suppressed — no fault analysis' : '✓ Anomaly confirmed — proceeding with diagnosis';
        const monAssetKey = msg.queriedAsset || (() => { try { return sessionStorage.getItem('dro-last-queried-asset'); } catch { return null; } })();
        if (monAssetKey) {
          updateAssetPipelineState(monAssetKey, {
            monitoringStatus: action === 'SUPPRESS' ? 'suppressed' : 'confirmed',
            monitoringAction: action, monitoringResolvedBy: persona,
          });
        }
        if (data.hitl_diagnosis) {
          appendA(`<span style="font-size:10px;color:#22c55e;font-weight:700">✓ ${label}</span>`, []);
          addMessage({ id: mkId(), type: 'hitl_diagnosis', data, scenario: msg.scenario, persona, queriedAsset: monAssetKey, time: ts() });
          if (msg.requestingPersona) {
            const actorName = PERSONA_DISPLAY_NAMES[persona] || persona;
            pushNotification(msg.requestingPersona, {
              id: mkId(), type: 'agent',
              html: `<div style="border:1.5px solid #22c55e;border-radius:8px;padding:10px 12px;background:rgba(34,197,94,0.05)"><div style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m);margin-bottom:5px">✓ FORWARDED REQUEST — MONITORING RESOLVED</div><strong style="color:var(--t)">Your forwarded anomaly review was actioned by ${actorName}.</strong><br><span style="font-size:11px;color:var(--t2)">Decision: <strong>${action === 'SUPPRESS' ? 'Suppressed — no fault analysis' : 'Confirmed — proceeding with fault diagnosis'}</strong>. Diagnosis gate is next.</span></div>`,
              routes: ['monitoring · forwarded result'], chips: [], time: ts(),
            });
          }
          return;
        }
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        if (data.hitl_advisory && data.hitl_advisory.advisory_note) addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
        if (data.hitl_knowledge) addMessage({ id: mkId(), type: 'hitl_knowledge', data, persona, time: ts() });
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
        if (msg.requestingPersona) {
          const actorName = PERSONA_DISPLAY_NAMES[persona] || persona;
          pushNotification(msg.requestingPersona, {
            id: mkId(), type: 'agent',
            html: `<div style="border:1.5px solid #22c55e;border-radius:8px;padding:10px 12px;background:rgba(34,197,94,0.05)"><div style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m);margin-bottom:5px">✓ FORWARDED REQUEST — MONITORING RESOLVED</div><strong style="color:var(--t)">Your forwarded anomaly review was actioned by ${actorName}.</strong><br><span style="font-size:11px;color:var(--t2)">Decision: <strong>${action === 'SUPPRESS' ? 'Suppressed — no fault analysis' : 'Confirmed — analysis proceeding'}</strong>. Pipeline has resumed.</span></div>`,
            routes: ['monitoring · forwarded result'], chips: [], time: ts(),
          });
        }
      } catch (err) { appendA('Monitoring HITL resolution failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">monitoring · HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f59e0b', borderRadius: '10px', padding: '14px', background: 'rgba(245,158,11,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#f59e0b', fontFamily: 'var(--m)' }}>ANOMALY REVIEW · Monitoring Agent</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: {resolverNames || 'Configured resolver'}</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>A borderline anomaly was detected. Confirm whether to investigate further or dismiss as normal.</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.9' }}>
              Anomaly score: <strong style={{ color: '#f59e0b' }}>{scorePct}</strong><br />
              Trigger: <strong>Single-sensor threshold exceeded</strong> — multivariate detector did not fire<br />
              EWMA signals: <strong>{ewmaSignals}</strong><br />
              Regime: {h.regime || 'normal running'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px', padding: '8px', background: 'rgba(245,158,11,0.06)', borderRadius: '6px', borderLeft: '2px solid #f59e0b' }}>
              {h.reason || 'EWMA fired on a single signal dimension but the joint multivariate detector did not trigger, which may indicate a startup transient or sensor noise rather than a real bearing defect.'}
            </div>
            {!resolved && canResolve ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve('SUPPRESS')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#f59e0b', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Dismiss — treat as normal
                </button>
                <button onClick={() => resolve('CONFIRM')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#ef4444', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Investigate — run fault diagnosis
                </button>
              </div>
            ) : resolved ? (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            ) : forwarded || msg.forwarded ? (
              <div style={{ fontSize: '11px', color: '#3b82f6', fontWeight: 700 }}>
                ✓ Sent to {msg.forwardedTo || h.access?.allowed_persona_names?.[0] || 'Responsible Approver'} — awaiting their decision
              </div>
            ) : (
              <div>
                <HITLReadOnlyNotice access={h.access} />
                <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                  <button
                    onClick={handleForward}
                    style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    Send to {h.access?.allowed_persona_names?.[0] || 'Responsible Approver'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
        {msg.chips && msg.chips.length > 0 && (
          <div className="qbrow">
            {msg.chips.map((c, i) => (
              <button key={i} className="qb b" onClick={() => onSq && onSq(c)}>{c}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HITLDiagnosisMsg({ msg, onSq, doThink, appendA, addMessage, persona }) {
  const { patchMessage, pushNotification, updateAssetPipelineState } = useContext(AppContext);
  const [resolved, setResolved] = useState(() => !!msg.resolved);
  const [forwarded, setForwarded] = useState(() => !!msg.forwarded);
  const h = msg.data.hitl_diagnosis;
  const canResolve = canResolveHITL(h.access, persona);
  const resolverNames = (h.access?.allowed_persona_names || []).join(' / ');

  async function handleForward() {
    const targetPersona = h.access?.allowed_personas?.[0];
    const targetPersonaName = h.access?.allowed_persona_names?.[0] || targetPersona || 'Responsible Approver';
    const senderName = PERSONA_DISPLAY_NAMES[persona] || persona;
    if (targetPersona) {
      try { await forwardHITL(rid, 'diagnosis', persona, targetPersona); } catch (err) {
        console.warn('Durable diagnosis hand-off failed:', err);
      }
    }
    setForwarded(true);
    patchMessage(msg.id, { forwarded: true, forwardedTo: targetPersonaName });
    if (targetPersona) {
      pushNotification(targetPersona, {
        id: mkId(), type: 'agent',
        html: `<div style="border:1.5px solid #3b82f6;border-radius:8px;padding:10px 12px;background:rgba(59,130,246,0.05)"><div style="font-size:9px;font-weight:700;color:#3b82f6;font-family:var(--m);margin-bottom:5px">FORWARDED HITL — FAILURE INTELLIGENCE</div><strong style="color:var(--t)">${senderName} has forwarded a fault classification review to you.</strong><br><span style="font-size:11px;color:var(--t2)">Low-confidence classification requires your expert confirmation before risk assessment proceeds. See the card below.</span></div>`,
        routes: ['failure intelligence · forwarded request'], chips: [], time: ts(),
      });
      pushNotification(targetPersona, { id: mkId(), type: 'hitl_diagnosis', data: msg.data, scenario: msg.scenario, persona: targetPersona, requestingPersona: persona, time: ts() });
    }
  }
  const rid = h.run_id;
  const confPct = h.confidence != null ? (h.confidence * 100).toFixed(0) + '%' : '—';
  const faultLabel = (h.fault_mode || 'unknown').replace(/_/g, ' ');
  const stageLabel = h.iso_stage > 0 ? 'ISO Stage ' + h.iso_stage : 'Stage unknown';

  async function resolve(action) {
    setResolved(true);
    patchMessage(msg.id, { resolved: true });
    doThink(async () => {
      try {
        const data = await resolveHITLDiagnosis(rid, action, persona);
        const label = action === 'CONFIRM' ? '✓ Classification confirmed' : '✓ Overridden to undetermined — manual inspection';
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · Pipeline resumed · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        const diagAssetKey = msg.queriedAsset || (() => { try { return sessionStorage.getItem('dro-last-queried-asset'); } catch { return null; } })();
        if (diagAssetKey) {
          updateAssetPipelineState(diagAssetKey, {
            diagnosisStatus: action === 'CONFIRM' ? 'confirmed' : 'overridden',
            diagnosisAction: action, diagnosisFaultLabel: faultLabel, diagnosisResolvedBy: persona,
          });
        }
        if (data.hitl_advisory && data.hitl_advisory.advisory_note) addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
        if (data.hitl_knowledge) addMessage({ id: mkId(), type: 'hitl_knowledge', data, persona, time: ts() });
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
        if (msg.requestingPersona) {
          const actorName = PERSONA_DISPLAY_NAMES[persona] || persona;
          const faultDesc = action === 'CONFIRM' ? `Confirmed: ${faultLabel}` : 'Overridden to undetermined — manual inspection required';
          pushNotification(msg.requestingPersona, {
            id: mkId(), type: 'agent',
            html: `<div style="border:1.5px solid #22c55e;border-radius:8px;padding:10px 12px;background:rgba(34,197,94,0.05)"><div style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m);margin-bottom:5px">✓ FORWARDED REQUEST — DIAGNOSIS RESOLVED</div><strong style="color:var(--t)">Your forwarded fault classification review was actioned by ${actorName}.</strong><br><span style="font-size:11px;color:var(--t2)">Decision: <strong>${faultDesc}</strong>. Risk assessment is now proceeding.</span></div>`,
            routes: ['failure intelligence · forwarded result'], chips: [], time: ts(),
          });
        }
      } catch (err) { appendA('Diagnosis HITL resolution failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">failure intelligence · HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f97316', borderRadius: '10px', padding: '14px', background: 'rgba(249,115,22,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#f97316', fontFamily: 'var(--m)' }}>FAULT CLASSIFICATION REVIEW · Failure Intelligence Agent</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: {resolverNames || 'Configured resolver'}</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>The AI detected a fault but confidence is below threshold. Please confirm or override before risk assessment continues.</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.9' }}>
              Classified fault: <strong>{faultLabel}</strong>{h.fault_code && <span style={{ color: 'var(--t3)' }}> ({h.fault_code})</span>}<br />
              Stage: <strong>{stageLabel}</strong><br />
              Confidence: <strong style={{ color: '#f97316' }}>{confPct}</strong> <span style={{ color: 'var(--t3)' }}>(threshold: 60%)</span>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t2)', marginBottom: '12px', padding: '8px 10px', background: 'rgba(249,115,22,0.06)', borderRadius: '6px', borderLeft: '2px solid #f97316' }}>
              {h.narrative || 'Insufficient signal evidence for a conclusive fault classification.'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px' }}>Confirm to proceed with this classification, or override to undetermined to trigger manual inspection.</div>
            {!resolved && canResolve ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve('CONFIRM')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#f97316', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Confirm: {faultLabel}
                </button>
                <button onClick={() => resolve('MARK_UNDETERMINED')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #6b7280', background: 'transparent', color: 'var(--t2)', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Override — needs manual inspection
                </button>
              </div>
            ) : resolved ? (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            ) : forwarded || msg.forwarded ? (
              <div style={{ fontSize: '11px', color: '#3b82f6', fontWeight: 700 }}>
                ✓ Sent to {msg.forwardedTo || h.access?.allowed_persona_names?.[0] || 'Responsible Approver'} — awaiting their decision
              </div>
            ) : (
              <div>
                <HITLReadOnlyNotice access={h.access} />
                <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                  <button
                    onClick={handleForward}
                    style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    Send to {h.access?.allowed_persona_names?.[0] || 'Responsible Approver'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
        {msg.chips && msg.chips.length > 0 && (
          <div className="qbrow">
            {msg.chips.map((c, i) => (
              <button key={i} className="qb b" onClick={() => onSq && onSq(c)}>{c}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HITLKnowledgeMsg({ msg, doThink, appendA, persona }) {
  const { patchMessage } = useContext(AppContext);
  const [resolved, setResolved] = useState(() => !!msg.resolved);
  const h = msg.data.hitl_knowledge;
  const canResolve = canResolveHITL(h.access, persona);
  const resolverNames = (h.access?.allowed_persona_names || []).join(' / ');
  const rid = h.run_id;
  const faultLabel = (h.fault_mode || 'unknown').replace(/_/g, ' ');
  const assetId = h.asset_id || 'unknown';

  async function resolve(action) {
    setResolved(true);
    patchMessage(msg.id, { resolved: true });
    doThink(async () => {
      try {
        const data = await resolveHITLKnowledge(rid, action, persona);
        const label = action === 'FLAG_MANUAL' ? '✓ Flagged for manual SOP lookup' : '✓ Proceeding without SOP';
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
      } catch (err) { appendA('Knowledge HITL resolution failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">knowledge · HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #06b6d4', borderRadius: '10px', padding: '14px', background: 'rgba(6,182,212,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#06b6d4', fontFamily: 'var(--m)' }}>NO PROCEDURE FOUND · Knowledge Agent</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: {resolverNames || 'Configured resolver'}</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>No standard procedure exists for this fault type — decide how to proceed</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.9' }}>
              Asset: <strong>{assetId}</strong><br />
              Fault mode: <strong>{faultLabel}</strong><br />
              <span style={{ color: '#ef4444', fontWeight: 600 }}>No SOP found in the catalog for this fault / asset combination.</span>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px' }}>
              A Safety Officer or Maintenance lead must confirm the correct procedure before any work begins. Alternatively, accept and apply general site maintenance guidelines.
            </div>
            {!resolved && canResolve ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve('FLAG_MANUAL')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#ef4444', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Escalate — get procedure manually
                </button>
                <button onClick={() => resolve('ACCEPT_EMPTY')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #06b6d4', background: 'transparent', color: '#06b6d4', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Continue without procedure
                </button>
              </div>
            ) : resolved ? (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            ) : (
              <HITLReadOnlyNotice access={h.access} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CrossAssetCorrelationMsg({ msg }) {
  const { correlation } = msg;
  const recommendations = [
    'Reliability Engineer to review both fault signatures for common-cause indicators',
    'Check whether last lubrication service covered both assets (CMMS log)',
    `Consider joint maintenance window for both — saves ${correlation.maintenanceSaving}`,
  ];

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'rgba(245,158,11,.2)', fontSize: '13px' }}>⚠</div>
      <div className="mb" style={{ maxWidth: '92%' }}>
        <div className="mmeta">
          <span className="msndr">DRO Correlation Engine</span>
          <span className="mtm">{msg.time}</span>
        </div>
        <div className="rrow">
          <span className="rs">cross-asset intelligence · pattern detected</span>
        </div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f59e0b', borderRadius: '10px', padding: '14px', background: 'rgba(245,158,11,.05)' }}>

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#f59e0b', fontFamily: 'var(--m)', letterSpacing: '.8px' }}>
                ⚠ CROSS-ASSET CORRELATION ALERT
              </span>
            </div>

            {/* Main statement */}
            <div style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--t)', marginBottom: '4px' }}>
              {correlation.asset2Id} is also CRITICAL within this shift window.
            </div>
            <div style={{ fontSize: '11.5px', color: 'var(--t2)', marginBottom: '10px' }}>
              Both <strong>{correlation.asset1Id}</strong> and <strong>{correlation.asset2Id}</strong> show:
            </div>

            {/* Match points */}
            <div style={{ padding: '8px 11px', background: 'rgba(245,158,11,.07)', borderRadius: '6px', borderLeft: '2px solid #f59e0b', marginBottom: '10px' }}>
              {correlation.matchPoints.map((point, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '7px', marginBottom: i < correlation.matchPoints.length - 1 ? '5px' : 0 }}>
                  <span style={{ color: '#f59e0b', fontSize: '9px', marginTop: '2px', flexShrink: 0 }}>—</span>
                  <span style={{ fontSize: '11.5px', color: 'var(--t2)' }}>{point}</span>
                </div>
              ))}
            </div>

            {/* Common-cause hypothesis */}
            <div style={{ fontSize: '11.5px', color: 'var(--t2)', marginBottom: '12px', padding: '8px 11px', background: 'rgba(245,158,11,.04)', borderRadius: '6px', lineHeight: '1.6' }}>
              This pattern is consistent with a{' '}
              <strong style={{ color: '#f59e0b' }}>common-cause event</strong>{' '}
              — possible contamination, shared lubrication batch, or line overload propagating across assets.
            </div>

            {/* Recommendations */}
            <div style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', fontWeight: 700, letterSpacing: '.7px', marginBottom: '8px' }}>
                RECOMMENDED BEFORE APPROVING {correlation.asset2Id}
              </div>
              {recommendations.map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: '8px', fontSize: '11.5px', color: 'var(--t2)', marginBottom: '6px' }}>
                  <span style={{ color: '#f59e0b', fontWeight: 700, flexShrink: 0, minWidth: '14px' }}>{i + 1}.</span>
                  <span>{r}</span>
                </div>
              ))}
            </div>

            {/* Financial summary */}
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', paddingTop: '9px', borderTop: '1px solid rgba(245,158,11,.2)', fontSize: '11px', color: 'var(--t3)' }}>
              {correlation.combinedExposure && (
                <span>
                  Combined financial exposure:{' '}
                  <strong style={{ color: '#f59e0b' }}>{correlation.combinedExposure}</strong>
                </span>
              )}
              {correlation.combinedCost && (
                <span>
                  Combined intervention cost:{' '}
                  <strong style={{ color: '#10b981' }}>~{correlation.combinedCost}</strong>
                </span>
              )}
              {correlation.partsOverlap && (
                <span>
                  Parts overlap:{' '}
                  <strong style={{ color: '#3b82f6' }}>
                    both require {correlation.faultClassLabel.includes('bearing') ? 'SKF bearing family' : 'same part family'} ✓
                  </strong>
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const REJECT_CODES = [
  { id: 'diagnosis_wrong', label: 'Diagnosis is wrong — fault type or severity is incorrect' },
  { id: 'parts_concern', label: 'Parts concern — availability or lead time does not work' },
  { id: 'second_opinion', label: 'Need second opinion from Reliability Engineer' },
  { id: 'wrong_window', label: 'Wrong maintenance window — cannot stop the line then' },
  { id: 'other', label: 'Other' },
];

function buildLearningHtml(lr) {
  const status = lr.learning_status || lr.status || 'unknown';
  const statusColors = { learned: '#10b981', duplicate: '#3b82f6', no_new_learning: '#6b7280', disabled: '#6b7280', failed: '#f59e0b', invalid_input: '#ef4444', persistence_failed: '#f59e0b' };
  const learningStatusLabels = { learned: 'NEW LEARNING STORED', duplicate: 'DUPLICATE - ALREADY KNOWN', no_new_learning: 'NO NEW LEARNING', disabled: 'LEARNING DISABLED', failed: 'STORAGE ERROR', invalid_input: 'INVALID INPUT - NOT STORED', persistence_failed: 'STORAGE ERROR' };
  const color = statusColors[status] || '#6b7280';
  const label = learningStatusLabels[status] || status.toUpperCase().replace(/_/g, ' ');
  let tagsHtml = '';
  if (lr.tags && lr.tags.length) {
    tagsHtml = `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px">`
      + lr.tags.map(t => `<span style="font-size:10px;padding:2px 7px;border-radius:3px;background:rgba(59,130,246,0.12);color:var(--t2)">${t.replace(/_/g, ' ')}</span>`).join('')
      + `</div>`;
  }

  let metaHtml = `<div style="font-size:12px;color:var(--t2);line-height:2;margin-bottom:8px">`;
  if (lr.case_id) metaHtml += `Case ID: <strong style="font-family:var(--m);color:${color}">${lr.case_id}</strong><br/>`;
  if (lr.fault_mode) metaHtml += `Fault mode: <strong>${lr.fault_mode}</strong><br/>`;
  if (lr.outcome) metaHtml += `Outcome: <strong>${lr.outcome}</strong><br/>`;
  if (lr.persistence_status && lr.persistence_status !== 'not_requested') metaHtml += `Persistence: <strong>${lr.persistence_status}</strong><br/>`;
  if (lr.status_reason || lr.reason) metaHtml += `<span style="color:#6b7280;font-size:11px">${lr.status_reason || lr.reason}</span><br/>`;
  metaHtml += `</div>`;

  const bodyText = lr.content || lr.summary || (
    status === 'no_new_learning'
      ? 'Agent 8 reviewed this turn; no new durable operational pattern was identified, so nothing was stored.'
      : status === 'disabled'
        ? 'Agent 8 observation is disabled by configuration; no learning record was written.'
        : ''
  );

  return `<div style="border:1.5px solid ${color};border-radius:10px;padding:14px;background:rgba(59,130,246,0.03);margin-top:2px">`
    + `<div style="font-size:9px;font-weight:700;color:${color};font-family:var(--m);margin-bottom:10px">PHASE 10 · LEARNING & MEMORY AGENT · ${label}</div>`
    + metaHtml
    + tagsHtml
    + (bodyText ? `<div style="font-size:11px;color:var(--t2);background:rgba(59,130,246,0.06);padding:9px 11px;border-radius:6px;border-left:2px solid ${color};line-height:1.6">${bodyText}</div>` : '')
    + `</div>`;
}

const _EXECUTOR_PERSONA_DISPLAY = {
  supervisor: 'Plant Supervisor', engineer: 'Reliability Engineer',
  maintenance: 'Maintenance Planner', manager: 'Plant Manager',
  executive: 'Executive', ot: 'OT Controls Operator',
  safety: 'Safety Officer', md: 'Managing Director',
};

function HITLExecutorMsg({ msg, doThink, appendA, persona }) {
  const [resolved, setResolved] = useState(() => msg.resolved || false);
  const [forwarded, setForwarded] = useState(() => !!msg.forwarded);
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [rejectCode, setRejectCode] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const { refreshNotifCounts, pushNotification, patchMessage, refreshWorkOrders, updateAssetPipelineState, updateAssetStatus, assets: allAssets } = useContext(AppContext);
  const rec = msg.rec;
  const canResolve = canResolveHITL(rec.hitl_access, persona);
  const decisionSupport = rec.decision_support || {};
  const escalation = decisionSupport.approval_escalation || {};
  const escalationStageIndex = Number(msg.escalationStageIndex || 0);
  const escalationStep = activeEscalationStep(
    escalation, escalationStageIndex, persona
  );
  const [escalationRemaining, setEscalationRemaining] = useState(() =>
    escalationStep ? remainingSeconds(escalationStep.escalates_at_utc) : null
  );
  const alternative = (rec.ranked_alternatives || [])[0];
  const action = rec.recommended_action;
  const urgencyColor = { immediate: '#ef4444', urgent: '#f97316', planned: '#3b82f6', monitor: '#6b7280' }[rec.urgency] || '#6b7280';
  const canConfirmReject = Boolean(rejectCode) && (rejectCode !== 'other' || Boolean(rejectReason.trim()));
  const currentApproverName = escalationStep?.from_persona_name
    || escalation.current_persona_name
    || rec.responsible_approver
    || 'Configured approver';
  const isFinalAuthority = escalation.status === 'final_authority'
    || (escalation.status === 'active' && !escalationStep && escalation.steps?.length > 0);

  useEffect(() => {
    if (!escalationStep || resolved || msg.escalationTriggered) return undefined;
    const updateCountdown = () => {
      setEscalationRemaining(remainingSeconds(escalationStep.escalates_at_utc));
    };
    updateCountdown();
    const interval = window.setInterval(updateCountdown, 1000);
    return () => window.clearInterval(interval);
  }, [escalationStep?.escalates_at_utc, resolved, msg.escalationTriggered]);

  async function handleForward() {
    // Try hitl_access first, then escalation chain as fallback
    const targetPersona = rec.hitl_access?.allowed_personas?.[0]
      || escalation.steps?.[0]?.to_persona_id;
    const targetPersonaName = rec.hitl_access?.allowed_persona_names?.[0]
      || escalation.steps?.[0]?.to_persona_name
      || rec.responsible_approver
      || 'Responsible Approver';
    const senderName = PERSONA_DISPLAY_NAMES[persona] || persona;
    if (targetPersona) {
      try {
        await forwardHITL(
          rec.hitl_run_id || rec.case_id || `EXEC-${rec.asset_id || 'unknown'}`,
          'execution', persona, targetPersona, '', rec.asset_id || '',
          rec.conversation_id || conversationIdRef.current,
        );
      } catch (err) {
        console.warn('Durable execution hand-off failed:', err);
      }
    }
    setForwarded(true);
    patchMessage(msg.id, { forwarded: true, forwardedTo: targetPersonaName });
    if (targetPersona) {
      pushNotification(targetPersona, {
        id: mkId(), type: 'agent',
        html: `<div style="border:1.5px solid #3b82f6;border-radius:8px;padding:10px 12px;background:rgba(59,130,246,0.05)"><div style="font-size:9px;font-weight:700;color:#3b82f6;font-family:var(--m);margin-bottom:5px">FORWARDED HITL — EXECUTOR</div><strong style="color:var(--t)">${senderName} has forwarded an execution approval request to you.</strong><br><span style="font-size:11px;color:var(--t2)">Asset <strong>${rec.asset_id}</strong> — maintenance action requires your approval before execution. See the card below.</span></div>`,
        routes: ['executor · forwarded request'], chips: [], time: ts(),
      });
      pushNotification(targetPersona, {
        id: mkId(),
        type: 'hitl_executor',
        rec,
        requestingPersona: persona,
        time: ts(),
      });
    }
  }

  async function resolve(approved, reason) {
    setResolved(true);
    patchMessage(msg.id, { resolved: true });
    doThink(async () => {
      try {
        const data = approved
          ? await runExecutor(rec, true, persona)
          : await rejectRecommendation(rec, rejectCode, reason || '', persona);
        refreshNotifCounts();
        if (approved) refreshWorkOrders();

        let html;
        if (approved) {
          const steps = data.execution_steps || [];
          const doneCount = data.completed_steps ?? steps.filter(s => s.status === 'done').length;
          const STEP_ICON = { done: '✅', pending: '⏳', blocked: '🔴' };
          const STEP_BG = { done: 'rgba(16,185,129,.04)', pending: 'rgba(245,158,11,.04)', blocked: 'rgba(239,68,68,.05)' };

          html = `<div style="border:1.5px solid #10b981;border-radius:10px;padding:14px;background:rgba(16,185,129,0.04)">`;
          html += `<div style="font-size:9px;font-weight:700;color:#10b981;font-family:var(--m);margin-bottom:8px">✓ APPROVED & EXECUTED · EXECUTOR RESULT</div>`;
          html += `<div style="font-size:13px;font-weight:600;color:var(--t);margin-bottom:2px">${doneCount} / ${steps.length} steps complete · ${friendlyActionLabel(data.action_taken)}</div>`;
          html += `<div style="font-size:10px;color:var(--t3);font-family:var(--m);margin-bottom:10px">`;
          if (data.work_order_id) html += `Work order: <span style="color:#10b981">${data.work_order_id}</span>`;
          if (data.audit_reference) html += ` · Audit: ${data.audit_reference}`;
          html += `</div>`;
          html += `<div style="display:flex;flex-direction:column;gap:4px">`;
          steps.forEach(step => {
            html += `<div style="display:flex;gap:8px;padding:7px 9px;border-radius:6px;background:${STEP_BG[step.status]};border:1px solid rgba(255,255,255,.05)">`;
            html += `<span style="font-size:11px;flex-shrink:0;margin-top:1px">${STEP_ICON[step.status]}</span>`;
            html += `<div style="flex:1"><div style="font-size:11.5px;font-weight:600;color:var(--t)">${step.step_name}</div>`;
            html += `<div style="font-size:10.5px;color:var(--t2);margin-top:2px">${step.detail}</div>`;
            if (step.owner) {
              html += `<div style="font-size:10px;color:#f97316;margin-top:3px">↳ Owner: ${step.owner}`;
              if (step.deadline) html += ` · deadline: ${step.deadline}`;
              if (step.escalates_to) html += ` · escalates to <strong>${step.escalates_to}</strong>`;
              if (step.escalation_rule) html += ` · ${step.escalation_rule}`;
              html += `</div>`;
            }
            html += `</div></div>`;
          });
          html += `</div></div>`;

          updateAssetPipelineState(rec.asset_id, {
            status: 'approved',
            woId: data.work_order_id || null,
            actionTaken: data.action_taken || null,
            partsReserved: data.parts_reserved || null,
            crewNotified: data.crew_notified || false,
            executionStatus: data.status || null,
            approvedBy: persona,
            approvedAt: new Date().toISOString(),
            originalRUL: decisionSupport.parts_vs_rul?.rul_min_days != null
              ? `${decisionSupport.parts_vs_rul.rul_min_days}–${decisionSupport.parts_vs_rul.rul_max_days} days`
              : null,
            originalHealth: (rec.urgency === 'immediate' || rec.urgency === 'urgent') ? 'critical' : 'warning',
          });
          // Persist WO display fields that the backend WO record won't carry —
          // risk level, cost, RUL window, duration and assigned persona
          if (data.work_order_id) {
            const pvr = decisionSupport.parts_vs_rul;
            // Execution crew = personas notified to carry out the work, excluding decision-maker roles
            const _EXEC_CREW_ROLES = new Set(['maintenance', 'engineer', 'safety', 'ot']);
            const _EXEC_MAP = {
              immediate: ['supervisor', 'engineer', 'maintenance', 'safety'],
              urgent:    ['supervisor', 'engineer', 'maintenance'],
              planned:   ['engineer', 'maintenance'],
              monitor:   ['engineer', 'ot'],
            };
            const execCrew = (_EXEC_MAP[(rec.urgency || 'planned').toLowerCase()] || ['maintenance'])
              .filter(p => _EXEC_CREW_ROLES.has(p))
              .map(p => _EXECUTOR_PERSONA_DISPLAY[p] || p);
            saveWOEnrichment(data.work_order_id, {
              risk: rec.urgency?.toUpperCase() || '—',
              cost: decisionSupport.cost_if_approved != null
                ? `$${decisionSupport.cost_if_approved.toLocaleString()}`
                : '—',
              rul: pvr?.rul_min_days != null
                ? `${pvr.rul_min_days}–${pvr.rul_max_days}d`
                : '—',
              est: action.estimated_duration_hours
                ? `${action.estimated_duration_hours}h`
                : '—',
              asgn: execCrew.join(' / ') || _EXECUTOR_PERSONA_DISPLAY[persona] || 'DRO Agent',
              parts: data.parts_reserved
                || rec.required_parts?.map(p => `${p.part_number} ×${p.quantity}`).join(', ')
                || '—',
              pr: rec.urgency === 'immediate' ? 'URGENT' : rec.urgency === 'urgent' ? 'URGENT' : 'MEDIUM',
            });
          }
          // Downgrade asset from critical → warning when WO is created (maintenance scheduled)
          const approvedAsset = (allAssets || []).find(a => a.id === rec.asset_id || a.asset_id === rec.asset_id);
          if (approvedAsset?.st === 'critical') {
            // Use frontend ID (M-104 style) so the chat status injection can match user queries
            updateAssetStatus(approvedAsset.id || rec.asset_id, 'warning');
          }
          // Notify relevant personas (mirrors backend _URGENCY_ROUTING)
          const URGENCY_RECIPIENTS = {
            immediate: ['supervisor', 'engineer', 'maintenance', 'safety'],
            urgent:    ['supervisor', 'engineer', 'maintenance'],
            planned:   ['engineer', 'maintenance'],
            monitor:   ['engineer', 'ot'],
          };
          const urgency = (rec.urgency || 'planned').toLowerCase();
          const recipients = URGENCY_RECIPIENTS[urgency] || ['maintenance'];
          recipients.forEach(p => {
            if (p !== persona) {
              pushNotification(p, { id: mkId(), type: 'agent', html, routes: ['executor · notification'], chips: [], time: ts() });
            }
          });
        } else {
          const selected = REJECT_CODES.find(code => code.id === rejectCode);
          const selectedLabel = rejectCode === 'other' && reason ? `Other — ${reason}` : selected?.label;
          html = `<div style="border:1.5px solid #ef4444;border-radius:10px;padding:14px;background:rgba(239,68,68,0.04)">`;
          html += `<div style="font-size:9px;font-weight:700;color:#ef4444;font-family:var(--m);margin-bottom:8px">✗ REJECTED · EXECUTOR RESULT</div>`;
          html += `<div style="font-size:12px;color:var(--t2);margin-bottom:8px">Recommendation rejected — no action taken.</div>`;
          html += `<div style="font-size:11px;color:var(--t3);padding:6px 10px;background:rgba(239,68,68,.04);border-radius:5px;border-left:2px solid rgba(239,68,68,.3)"><strong style="color:var(--t2)">${selectedLabel}</strong></div>`;
          html += `<div style="font-size:10.5px;color:var(--t2);margin-top:8px">Learning feedback: <strong>${data.status}</strong> · stored: ${data.persistence_status} · consecutive rejections: ${data.consecutive_rejections}</div>`;
          if (data.reliability_review_required) html += `<div style="font-size:11px;color:#f97316;margin-top:6px;font-weight:700">Reliability Engineer review required — ${data.review_reason}</div>`;
          html += `</div>`;
          updateAssetPipelineState(rec.asset_id, {
            status: 'rejected',
            rejectCode,
            rejectedBy: persona,
            rejectedAt: new Date().toISOString(),
          });
        }

        // Notify requesting persona if this card was forwarded to us
        if (msg.requestingPersona) {
          const actorName = _EXECUTOR_PERSONA_DISPLAY[persona] || persona;
          const notifHtml = approved
            ? `<div style="border:1.5px solid #10b981;border-radius:8px;padding:10px 12px"><div style="font-size:9px;font-weight:700;color:#10b981;font-family:var(--m);margin-bottom:5px">✓ FORWARDED REQUEST — APPROVED</div><strong style="color:var(--t)">Your forwarded request for ${rec.asset_id} was approved.</strong><br><span style="font-size:11px;color:var(--t2)">Work order <strong style="color:#10b981">${data.work_order_id || 'created'}</strong> · actioned by ${actorName}</span></div>`
            : `<div style="border:1.5px solid #ef4444;border-radius:8px;padding:10px 12px"><div style="font-size:9px;font-weight:700;color:#ef4444;font-family:var(--m);margin-bottom:5px">✗ FORWARDED REQUEST — REJECTED</div><strong style="color:var(--t)">Your forwarded request for ${rec.asset_id} was rejected by ${actorName}.</strong></div>`;
          pushNotification(msg.requestingPersona, { id: mkId(), type: 'agent', html: notifHtml, routes: ['executor · forwarded result'], chips: [], time: ts() });
        }
        appendA(html, approved ? ['executor · result'] : ['learning feedback']);
      } catch (err) {
        const msg403 = err.message?.includes('403')
          ? 'This persona does not have execution authority. Switch to a persona with approval rights (Supervisor, Manager, or Maintenance Planner).'
          : 'Executor call failed: ' + err.message;
        appendA(msg403, []);
      }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '92%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">executor · approval gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #10b981', borderRadius: '10px', padding: '14px', background: 'rgba(16,185,129,0.06)' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#10b981', fontFamily: 'var(--m)' }}>APPROVAL REQUIRED · Executor Agent</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: {currentApproverName}</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>Review and approve this maintenance action — a work order will be created automatically once approved</strong>

            {/* Core details */}
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '2' }}>
              Case: <strong>{rec.case_id}</strong> &nbsp;|&nbsp; Asset: <strong>{rec.asset_id}</strong><br />
              Action: <strong>{friendlyActionLabel(action.name)}</strong><br />
              Urgency: <strong style={{ color: urgencyColor }}>{rec.urgency.toUpperCase()}</strong>
              &nbsp;|&nbsp; Est. duration: <strong>{action.estimated_duration_hours}h</strong><br />
              Parts: {rec.required_parts.map(p => (
                <span key={p.part_number} style={{ background: 'rgba(16,185,129,.1)', border: '1px solid #10b981', borderRadius: '4px', padding: '2px 8px', fontSize: '11px', color: '#10b981', marginRight: '4px' }}>
                  {p.part_number} ×{p.quantity}
                </span>
              ))}<br />

            </div>

            {/* Rationale */}
            {rec.rationale && (
              <div style={{ fontSize: '11px', color: 'var(--t2)', background: 'rgba(16,185,129,0.06)', padding: '9px 11px', borderRadius: '6px', borderLeft: '2px solid #10b981', marginBottom: '12px', lineHeight: '1.6' }}>
                <div style={{ fontSize: '10px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', textTransform: 'uppercase', marginBottom: '4px' }}>Why this action was recommended</div>
                {cleanRationale(rec.rationale)}
              </div>
            )}

            {/* A4 — backend-scheduled approval escalation */}
            {escalationStep && (
              <div style={{ marginBottom: '10px', padding: '9px 11px', borderRadius: '7px', border: '1px solid rgba(249,115,22,.32)', background: 'rgba(249,115,22,.06)', display: 'flex', gap: '12px', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ fontSize: '9px', color: '#f97316', fontFamily: 'var(--m)', fontWeight: 700, letterSpacing: '.7px', marginBottom: '3px' }}>AUTO-ESCALATION</div>
                  <div style={{ fontSize: '10.5px', color: 'var(--t2)' }}>
                    If unanswered, escalates to <strong>{escalationStep.to_persona_name}</strong>
                  </div>
                  <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px' }}>
                    Exact time: {formatEscalationTime(escalationStep.escalates_at_utc)}
                  </div>
                </div>
                <div aria-label="approval escalation countdown" style={{ color: '#f97316', fontFamily: 'var(--m)', fontSize: '16px', fontWeight: 700, flexShrink: 0 }}>
                  {escalationRemaining === 0 ? 'ESCALATING...' : formatCountdown(escalationRemaining)}
                </div>
              </div>
            )}
            {isFinalAuthority && !resolved && (
              <div style={{ marginBottom: '10px', padding: '8px 11px', borderRadius: '7px', border: '1px solid rgba(100,116,139,.25)', color: 'var(--t3)', fontSize: '10.5px' }}>
                Final approval authority — no further automatic escalation.
              </div>
            )}

            {/* A3 — Decision Support block (only when enriched fields are actually present) */}
            {!!(decisionSupport.cost_data_status !== 'unavailable' || decisionSupport.historical_cases?.length > 0 || (decisionSupport.parts_vs_rul && decisionSupport.parts_vs_rul.status !== 'not_required') || decisionSupport.authority_check !== 'not_evaluated') && (
              <div style={{ marginBottom: '10px', border: '1px solid rgba(16,185,129,.22)', borderRadius: '8px', overflow: 'hidden', fontSize: '11px' }}>
                <div style={{ padding: '5px 11px', background: 'rgba(16,185,129,.1)', fontSize: '9px', fontWeight: 700, color: '#10b981', fontFamily: 'var(--m)', letterSpacing: '.8px' }}>
                  DECISION SUPPORT
                </div>

                {/* Cost comparison */}
                {decisionSupport.cost_if_approved != null && decisionSupport.cost_if_deferred != null && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid rgba(16,185,129,.15)' }}>
                    <div style={{ padding: '8px 11px', borderRight: '1px solid rgba(16,185,129,.15)' }}>
                      <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '3px' }}>COST IF APPROVED</div>
                      <div style={{ fontSize: '15px', fontWeight: 700, color: '#10b981' }}>{decisionSupport.currency} {decisionSupport.cost_if_approved.toLocaleString()}</div>
                      <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px' }}>{decisionSupport.cost_breakdown}</div>
                    </div>
                    <div style={{ padding: '8px 11px' }}>
                      <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '3px' }}>COST IF DEFERRED</div>
                      <div style={{ fontSize: '15px', fontWeight: 700, color: '#ef4444' }}>{decisionSupport.currency} {decisionSupport.cost_if_deferred.toLocaleString()}</div>
                      <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px' }}>{decisionSupport.currency} {(decisionSupport.deferred_cost_per_hour || 0).toLocaleString()}/hr · {decisionSupport.cost_basis}</div>
                    </div>
                  </div>
                )}

                {/* Parts vs RUL window */}
                {decisionSupport.parts_vs_rul && decisionSupport.parts_vs_rul.status !== 'not_required' && (
                  <div style={{ padding: '7px 11px', borderBottom: '1px solid rgba(16,185,129,.15)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--t3)', fontSize: '10.5px' }}>Parts ETA vs RUL window</span>
                    <span style={{ color: decisionSupport.parts_vs_rul.status === 'within_rul_window' ? '#10b981' : '#ef4444', fontWeight: 600, fontFamily: 'var(--m)', fontSize: '10px' }}>
                      {decisionSupport.parts_vs_rul.part_number} · ETA {decisionSupport.parts_vs_rul.eta_days ?? 'unknown'}d · RUL {decisionSupport.parts_vs_rul.rul_min_days}-{decisionSupport.parts_vs_rul.rul_max_days}d · {decisionSupport.parts_vs_rul.status.replace(/_/g, ' ')}
                    </span>
                  </div>
                )}

                {/* Historical cases */}
                {decisionSupport.historical_cases && decisionSupport.historical_cases.length > 0 && (
                  <div style={{ padding: '7px 11px', borderBottom: '1px solid rgba(16,185,129,.15)' }}>
                    <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '5px', letterSpacing: '.5px' }}>HISTORICAL PRECEDENT</div>
                    {decisionSupport.historical_cases.map((c, i) => {
                      const ok = !c.outcome.toLowerCase().includes('fail') && !c.outcome.toLowerCase().includes('shutdown');
                      return (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: i < decisionSupport.historical_cases.length - 1 ? '4px' : 0 }}>
                          <span style={{ color: ok ? '#10b981' : '#ef4444', fontSize: '10px', flexShrink: 0 }}>{ok ? '✓' : '✗'}</span>
                          <span style={{ color: 'var(--t3)', fontSize: '9px', fontFamily: 'var(--m)', flexShrink: 0, width: '92px' }}>{c.recorded_at || c.case_id}</span>
                          <span style={{ color: 'var(--t2)', fontSize: '10.5px', flex: 1 }}>{friendlyActionLabel(c.action_taken)} → <span style={{ color: ok ? '#10b981' : '#ef4444' }}>{c.outcome}</span> · {c.source}</span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Authority check */}
                {decisionSupport.authority_check !== 'not_evaluated' && (
                  <div style={{ padding: '7px 11px', borderBottom: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--t3)', fontSize: '10.5px' }}>Approver authority</span>
                    <span style={{ color: decisionSupport.authority_check === 'within_authority' ? '#10b981' : '#f97316', fontWeight: 600, fontSize: '10px' }}>
                      {decisionSupport.authority_reason}
                    </span>
                  </div>
                )}

              </div>
            )}

            {/* Action buttons + A5 rejection form */}
            {!resolved && canResolve ? (
              <>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button
                    onClick={() => resolve(true)}
                    title="Approve the recommendation and trigger automated execution steps"
                    style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#10b981', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    Approve &amp; Execute
                  </button>
                  <button
                    onClick={() => { setShowRejectInput(s => !s); setRejectCode(''); setRejectReason(''); }}
                    title="Reject this recommendation and log the reason"
                    style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #ef4444', background: 'transparent', color: '#ef4444', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    {showRejectInput ? 'Cancel' : 'Reject'}
                  </button>
                </div>
                <div style={{ marginTop: '8px', fontSize: '10px', color: 'var(--t3)', fontFamily: 'var(--m)', letterSpacing: '.3px' }}>
                  Execution authority: Plant Supervisor · Plant Manager · Maintenance Planner
                </div>

                {showRejectInput && (
                  <div style={{ marginTop: '12px', padding: '10px 12px', background: 'rgba(239,68,68,.04)', border: '1px solid rgba(239,68,68,.2)', borderRadius: '7px' }}>
                    <div style={{ fontSize: '9px', color: '#ef4444', fontFamily: 'var(--m)', fontWeight: 700, marginBottom: '9px', letterSpacing: '.6px' }}>SELECT REJECTION REASON</div>
                    {REJECT_CODES.map(code => (
                      <label key={code.id} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', marginBottom: '8px', cursor: 'pointer' }}>
                        <input
                          type="radio"
                          name={`reject-reason-${msg.id}`}
                          value={code.id}
                          checked={rejectCode === code.id}
                          onChange={() => { setRejectCode(code.id); if (code.id !== 'other') setRejectReason(''); }}
                          style={{ marginTop: '2px', accentColor: '#ef4444', flexShrink: 0 }}
                        />
                        <span style={{ fontSize: '11.5px', color: 'var(--t2)', lineHeight: 1.5 }}>{code.label}</span>
                      </label>
                    ))}
                    {rejectCode === 'other' && (
                      <textarea
                        value={rejectReason}
                        onChange={e => setRejectReason(e.target.value)}
                        placeholder="Describe the reason…"
                        rows={2}
                        autoFocus
                        style={{ width: '100%', padding: '7px 10px', borderRadius: '6px', border: '1px solid rgba(239,68,68,.35)', background: 'rgba(239,68,68,.03)', color: 'var(--t)', fontSize: '11px', resize: 'none', fontFamily: 'var(--f)', outline: 'none', lineHeight: 1.5, marginBottom: '6px' }}
                      />
                    )}
                    <button
                      disabled={!canConfirmReject}
                      onClick={() => resolve(false, rejectReason.trim())}
                      style={{ marginTop: rejectCode === 'other' ? '2px' : '6px', padding: '6px 14px', borderRadius: '6px', border: 'none', background: canConfirmReject ? '#ef4444' : '#374151', color: '#fff', fontWeight: 700, cursor: canConfirmReject ? 'pointer' : 'not-allowed', fontSize: '11px', opacity: canConfirmReject ? 1 : 0.5 }}>
                      Confirm rejection
                    </button>
                  </div>
                )}
              </>
            ) : !resolved ? (
              forwarded || msg.forwarded ? (
                <div style={{ fontSize: '11px', color: '#3b82f6', fontWeight: 700 }}>
                  ✓ Sent to {msg.forwardedTo || rec.hitl_access?.allowed_persona_names?.[0]} — awaiting their decision
                </div>
              ) : (
                <div>
                  <HITLReadOnlyNotice access={rec.hitl_access} />
                  <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                    <button
                      onClick={handleForward}
                      style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                      Send to {rec.hitl_access?.allowed_persona_names?.[0] || escalation.steps?.[0]?.to_persona_name || rec.responsible_approver || 'Responsible Approver'}
                    </button>
                  </div>
                </div>
              )
            ) : msg.escalationTriggered ? (
              <div style={{ fontSize: '11px', color: '#f97316', fontWeight: 700 }}>
                Escalated automatically to {msg.escalatedTo || escalationStep?.to_persona_name}
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Processing…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
