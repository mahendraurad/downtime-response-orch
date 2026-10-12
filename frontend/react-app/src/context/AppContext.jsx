import React, { createContext, useState, useCallback, useEffect, useRef } from 'react';
import { ASSETS } from '../data/assets';
import { WOS } from '../data/workOrders';
import { getNotifCounts, getNotifs, getPendingHITL, postPersonaNotification, forwardHITL } from '../api/pipeline';
import { fetchDashboardAssets } from '../api/dashboard';
import { fetchWorkOrders, patchWorkOrder } from '../api/workOrders';
import { useAuth } from './AuthContext';
import { activeEscalationStep, remainingSeconds } from '../utils/escalationTimer';
import { ts } from '../utils/helpers';

export const AppContext = createContext(null);

// Bump the UI history key whenever response authority changes. This prevents
// cards rendered by older bundles from reappearing after deployment.
const SESSION_KEY = 'dro-chat-v2';
const ASSET_PIPELINE_KEY = 'dro-asset-pipeline';

const WO_FALLBACK = Object.fromEntries(WOS.map(wo => [wo.id, wo]));

function loadAssetPipeline() {
  try {
    const raw = sessionStorage.getItem(ASSET_PIPELINE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveAssetPipeline(map) {
  try {
    sessionStorage.setItem(ASSET_PIPELINE_KEY, JSON.stringify(map));
  } catch {}
}

// Enrichment data saved at executor HITL approval time — carries risk, cost, RUL,
// duration and assignee from the recommendation rec into the WO display fields
// (the backend WO record doesn't include these).
const WO_ENRICH_KEY = 'dro-wo-enrichments';
export function saveWOEnrichment(woId, fields) {
  try {
    const map = JSON.parse(sessionStorage.getItem(WO_ENRICH_KEY) || '{}');
    sessionStorage.setItem(WO_ENRICH_KEY, JSON.stringify({ ...map, [woId]: fields }));
  } catch {}
}
function loadWOEnrichments() {
  try { return JSON.parse(sessionStorage.getItem(WO_ENRICH_KEY) || '{}'); } catch { return {}; }
}

function mergeWOs(payload) {
  const enrichments = loadWOEnrichments();
  return (payload.workorders || []).map(wo => {
    const fb = WO_FALLBACK[wo.id] || {};
    const en = enrichments[wo.id] || {};
    return {
      ...fb,
      id: wo.id,
      pr: wo.priority || en.pr || 'MEDIUM',
      st: wo.status || 'Pending',
      ti: wo.title || wo.description || wo.id,
      as: wo.asset_display || wo.asset_id || fb.as || '',
      asgn: wo.assigned_to !== 'Pending' && wo.assigned_to ? wo.assigned_to : (en.asgn || 'Pending'),
      due: wo.due || 'Not scheduled',
      by: wo.created_by || 'DRO Agent',
      tp: wo.type || 'Maintenance',
      est: wo.est_hours ? `${wo.est_hours}h` : (en.est || fb.est || '—'),
      parts: wo.parts || en.parts || fb.parts || '',
      rul: wo.rul ?? en.rul ?? fb.rul ?? '—',
      risk: wo.risk || en.risk || fb.risk || '—',
      cost: wo.cost ?? en.cost ?? fb.cost ?? '—',
      cl: (wo.checklist || []).map(item => ({ ck: Boolean(item.done), tx: item.text, tg: item.tag || 'Task' })),
      tl: wo.timeline?.length
        ? wo.timeline.map(ev => ({ dot: ev.dot || 'var(--t3)', ev: ev.ev, tm: ev.tm }))
        : (fb.tl || []),
      case_id: wo.case_id || '',
      fault_mode: wo.fault_mode || '',
      rejection_reason_code: wo.rejection_reason_code || '',
      rejection_reason: wo.rejection_reason || '',
      agent8_learning: wo.agent8_learning || null,
      backend_source: 'api/workorders',
    };
  });
}
const MAX_MSGS_PER_PERSONA = 120;

function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveSession(map) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(map));
  } catch {
    // sessionStorage full — silently skip
  }
}

function escapeNotificationText(value) {
  return String(value || '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

export function AppProvider({ children }) {
  const { user } = useAuth();
  const defaultPersona = user?.allowed_personas?.[0] || 'supervisor';
  const [persona, setPersona] = useState(defaultPersona);
  const [currentView, setCurrentView] = useState('chat');
  const [agentAsset, setAgentAsset] = useState('M-104');
  const [assets, setAssets] = useState(ASSETS);
  const [assetsLoaded, setAssetsLoaded] = useState(false);
  const [workOrders, setWorkOrders] = useState([]);
  const [selectedAsset, setSelectedAsset] = useState(ASSETS[0]);
  const [selectedWO, setSelectedWO] = useState(null);
  const [dataStatus, setDataStatus] = useState({ assets: 'fallback', workOrders: 'fallback' });
  const [selectedNode, setSelectedNode] = useState(0);
  const [currentAgTab, setCurrentAgTab] = useState('overview');
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [theme, setThemeState] = useState(() => localStorage.getItem('dro-theme') || 'light');
  const [notifCounts, setNotifCounts] = useState({});
  const [showLeftPanel, setShowLeftPanel] = useState(true);
  const [assetPipelineState, setAssetPipelineState] = useState(loadAssetPipeline);

  const updateAssetPipelineState = useCallback((assetId, patch) => {
    setAssetPipelineState(prev => {
      const next = { ...prev, [assetId]: { ...(prev[assetId] || {}), ...patch } };
      saveAssetPipeline(next);
      return next;
    });
  }, []);

  const updateAssetStatus = useCallback((assetId, newStatus) => {
    const applyStatus = a =>
      (a.id === assetId || a.asset_id === assetId) ? { ...a, st: newStatus } : a;
    setAssets(prev => prev.map(applyStatus));
    setSelectedAsset(prev => prev ? applyStatus(prev) : prev);
  }, []);

  // Update live sensor readings on a specific asset so fleet ribbon + registry stay current
  const updateAssetSensorData = useCallback((assetId, sensorFields) => {
    const applySensor = a => {
      if (a.id !== assetId && a.asset_id !== assetId) return a;
      return {
        ...a,
        vib:  sensorFields.vib_rms_mms  != null ? +sensorFields.vib_rms_mms.toFixed(2)  : a.vib,
        tmp:  sensorFields.temp_c        != null ? +sensorFields.temp_c.toFixed(1)        : a.tmp,
        bpfo: sensorFields.bpfo_ratio    != null ? +sensorFields.bpfo_ratio.toFixed(3)    : a.bpfo,
      };
    };
    setAssets(prev => prev.map(applySensor));
    setSelectedAsset(prev => prev ? applySensor(prev) : prev);
  }, []);

  useEffect(() => {
    let active = true;
    const fallbackByAsset = Object.fromEntries(ASSETS.map(a => [a.id, a]));
    fetchDashboardAssets().then(payload => {
      if (!active) return;
      const rows = (payload.assets || []).map(a => {
        const fallback = fallbackByAsset[a.id] || {};
        return {
          ...fallback,
          id: a.id, asset_id: a.asset_id, nm: a.name, tp: a.type,
          br: a.bearing_model, kw: a.power_kw, rpm: a.rpm, ln: a.line,
          st: a.status, rul: a.rul, vib: a.vibration, tmp: a.temperature,
          bpfo: a.bpfo, iso: `Zone ${a.iso_zone}`, trend: a.trend || [],
          scenario: a.scenario || null,
          faults: (a.faults || []).map(f => ({
            dt: f.date, ty: f.type, st: `${f.stage} ${f.status}`,
            sc: a.status === 'critical' ? 'var(--rd)' : 'var(--am)',
          })),
          backend_source: a.source,
        };
      });
      if (rows.length) {
        // The dashboard API is authoritative. Lifecycle status is derived from
        // the durable work-order repository; session state must not overwrite it.
        setAssets(rows);
        setSelectedAsset(current => rows.find(a => a.id === current?.id) || rows[0]);
        setDataStatus(current => ({ ...current, assets: payload.source || 'backend' }));
      }
      setAssetsLoaded(true);
    }).catch(() => {
      setDataStatus(current => ({ ...current, assets: 'fallback' }));
      setAssetsLoaded(true);
    });

    fetchWorkOrders().then(payload => {
      if (!active) return;
      const rows = mergeWOs(payload);
      if (rows.length) {
        // Work-order status comes from the backend repository. Do not let
        // browser session state mask a failed or stale persistence update.
        setWorkOrders(rows);
        setSelectedWO(current => rows.find(wo => wo.id === current?.id) || rows[0]);
        setDataStatus(current => ({ ...current, workOrders: 'backend' }));
      }
    }).catch(() => setDataStatus(current => ({ ...current, workOrders: 'fallback' })));

    // Poll every 30s to pick up WOs created by backend agents (e.g. HITL executor)
    const pollInterval = setInterval(() => {
      if (!active) return;
      fetchWorkOrders().then(payload => {
        if (!active) return;
        const rows = mergeWOs(payload);
        if (rows.length) {
          setWorkOrders(prev => {
            // Only update if something actually changed (new WO count or status diff)
            const hasNew = rows.length !== prev.length ||
              rows.some(r => {
                const existing = prev.find(p => p.id === r.id);
                return !existing || existing.st !== r.st;
              });
            return hasNew ? rows : prev;
          });
        }
      }).catch(() => {});
    }, 30_000);

    return () => { active = false; clearInterval(pollInterval); };
  }, []);

  // Per-persona message store, backed by sessionStorage so history survives persona switches
  const [messagesMap, setMessagesMap] = useState(loadSession);

  // Hydrate backend-delivered notifications into the selected persona's chat
  // stream.  Cross-persona actions therefore remain visible after a refresh
  // instead of existing only in the originating browser tab.
  useEffect(() => {
    let active = true;
    getNotifs(persona).then(payload => {
      if (!active) return;
      const remote = (payload.notifications || []).map(item => ({
        id: `remote-${item.id}`,
        type: 'agent',
        html: `<strong>${escapeNotificationText(item.title || 'DRO notification')}</strong><br>${escapeNotificationText(item.message || '')}`,
        routes: ['notifications', item.type || 'agent'],
        chips: [],
        time: item.sent_at || ts(),
      }));
      if (!remote.length) return;
      setMessagesMap(previous => {
        const current = previous[persona] || [];
        const known = new Set(current.map(message => message.id));
        const additions = remote.filter(message => !known.has(message.id));
        if (!additions.length) return previous;
        const next = {
          ...previous,
          [persona]: [...current, ...additions].slice(-MAX_MSGS_PER_PERSONA),
        };
        saveSession(next);
        return next;
      });
    }).catch(() => {});
    return () => { active = false; };
  }, [persona]);

  // Rebuild actionable HITL cards from the durable repository.  Notifications
  // are intentionally only a badge/summary; the pending endpoint carries the
  // card projection so a refresh or a different browser cannot lose the task.
  useEffect(() => {
    let active = true;
    getPendingHITL(persona).then(payload => {
      if (!active) return;
      const items = payload.items || [];
      const pendingRunIds = new Set(items.map(item => String(item.run_id || '')));
      const additions = items.flatMap(item => {
        const card = item.hitl_payload || {};
        const base = {
          id: `durable-hitl-${item.run_id}`,
          time: item.created_at ? new Date(item.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ts(),
          durable: true,
          requestingPersona: card.requesting_persona || undefined,
          escalationStageIndex: Number(card.escalation_stage_index || 0),
        };
        if (item.gate === 'execution' && card.recommendation) {
          return [{ ...base, type: 'hitl_executor', rec: card.recommendation }];
        }
        if (item.gate === 'remediation') {
          return [{ ...base, type: 'hitl_remediation', data: { hitl_required: card }, persona }];
        }
        if (item.gate === 'diagnosis') {
          return [{ ...base, type: 'hitl_diagnosis', data: { hitl_diagnosis: card }, persona }];
        }
        return [];
      });
      setMessagesMap(previous => {
        const current = previous[persona] || [];
        const known = new Set(current.map(message => message.id));
        const fresh = additions.filter(message => !known.has(message.id));
        // Reconcile unresolved durable cards with the repository.  A browser
        // session can outlive an approval/closure; keeping its stale card is
        // what made a healthy asset show HITL again after refresh.
        const reconciled = current.filter(message => {
          if (message.type !== 'hitl_executor' || !message.durable || message.resolved) {
            return true;
          }
          const runId = String(message.rec?.hitl_run_id || '').trim();
          return runId ? pendingRunIds.has(runId) : true;
        });
        if (!fresh.length && reconciled.length === current.length) return previous;
        const next = { ...previous, [persona]: [...reconciled, ...fresh].slice(-MAX_MSGS_PER_PERSONA) };
        saveSession(next);
        return next;
      });
    }).catch(() => {});
    return () => { active = false; };
  }, [persona]);

  // A4 escalation worker scans every persona inbox, not only the card currently
  // visible on screen. Expired approvals therefore transfer after persona
  // switches and immediately after a session is restored.
  const escalationInFlightRef = useRef(new Set());
  useEffect(() => {
    const interval = window.setInterval(() => {
      setMessagesMap(previous => {
        const nowMs = Date.now();
        let changed = false;
        const transfers = [];
        const updated = {};

        Object.entries(previous).forEach(([personaId, rows]) => {
          updated[personaId] = rows.map(message => {
            if (message.type !== 'hitl_executor' || message.resolved || message.escalationTriggered) {
              return message;
            }
            const stageIndex = Number(message.escalationStageIndex || 0);
            const runKey = String(message.rec?.hitl_run_id || message.rec?.case_id || message.id);
            if (escalationInFlightRef.current.has(runKey)) {
              return message;
            }
            const escalation = message.rec?.decision_support?.approval_escalation;
            const step = activeEscalationStep(escalation, stageIndex, personaId);
            if (!step || remainingSeconds(step.escalates_at_utc, nowMs) !== 0) {
              return message;
            }
            escalationInFlightRef.current.add(runKey);
            changed = true;
            const transferId = `${message.id}-escalation-${stageIndex + 1}`;
            const target = step.to_persona_id;
            const allowed = message.rec?.hitl_access?.allowed_personas || [];
            const targetAccess = message.rec?.hitl_access
              ? {
                  ...message.rec.hitl_access,
                  selected_persona: target,
                  can_resolve: allowed.includes(target),
                  mode: allowed.includes(target) ? 'actionable' : 'read_only',
                }
              : undefined;
            transfers.push({
              target,
              message: {
                id: transferId,
                type: 'hitl_executor',
                rec: targetAccess ? { ...message.rec, hitl_access: targetAccess } : message.rec,
                durable: true,
                requestingPersona: message.requestingPersona || personaId,
                escalationStageIndex: stageIndex + 1,
                escalatedFrom: step.from_persona_name,
                time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              },
            });
            forwardHITL(
              message.rec?.hitl_run_id || message.rec?.case_id || '',
              'execution', personaId, step.to_persona_id,
              'Automatic escalation after approval deadline.',
              message.rec?.asset_id || '',
              message.rec?.conversation_id || null,
              stageIndex + 1,
            ).catch(() => {
              // Keep the card actionable if the durable transfer failed; the
              // next interval may retry after the transient error clears.
              escalationInFlightRef.current.delete(runKey);
            });
            // Keep the notification durable as well; the target persona will
            // hydrate the actual card from /api/pipeline/hitl/pending.
            postPersonaNotification(step.to_persona_id, {
              type: 'hitl_executor',
              title: 'HITL approval escalated',
              message: `Approval for ${message.rec?.asset_id || 'asset'} escalated from ${step.from_persona_name}.`,
              run_id: message.rec?.hitl_run_id || message.rec?.case_id || '',
              asset_id: message.rec?.asset_id || '',
              metadata: { hitl_card: true, gate: 'execution', escalation_stage_index: stageIndex + 1 },
            }).catch(() => {});
            return {
              ...message,
              resolved: true,
              escalationTriggered: true,
              escalatedTo: step.to_persona_name,
            };
          });
        });

        transfers.forEach(({ target, message }) => {
          const rows = updated[target] || [];
          if (!rows.some(existing => existing.id === message.id)) {
            updated[target] = [...rows, message].slice(-MAX_MSGS_PER_PERSONA);
          }
        });
        if (!changed) return previous;
        saveSession(updated);
        return updated;
      });
    }, 1000);
    return () => window.clearInterval(interval);
  }, []);

  const messages = messagesMap[persona] || [];

  const _update = useCallback((pid, fn) => {
    setMessagesMap(prev => {
      const next = fn(prev[pid] || []);
      // Cap per-persona history to avoid sessionStorage bloat
      const capped = next.length > MAX_MSGS_PER_PERSONA
        ? next.slice(next.length - MAX_MSGS_PER_PERSONA)
        : next;
      const updated = { ...prev, [pid]: capped };
      saveSession(updated);
      return updated;
    });
  }, []);

  const setMessages = useCallback((msgs) => {
    _update(persona, () => typeof msgs === 'function' ? msgs(messagesMap[persona] || []) : msgs);
  }, [persona, _update, messagesMap]);

  const addMessage = useCallback((msg) => {
    _update(persona, prev => [...prev, msg]);
  }, [persona, _update]);

  const clearMessages = useCallback(() => {
    _update(persona, () => []);
  }, [persona, _update]);

  // Patch a field on one message in the current persona's history (e.g. resolved: true)
  const patchMessage = useCallback((id, patch) => {
    _update(persona, prev => prev.map(m => m.id === id ? { ...m, ...patch } : m));
  }, [persona, _update]);

  // Local notification counts for cross-persona forwards (not polled from backend)
  const [localNotifCounts, setLocalNotifCounts] = useState({});

  const clearLocalNotif = useCallback((pid) => {
    setLocalNotifCounts(prev => ({ ...prev, [pid]: 0 }));
  }, []);

  // Push a message into a DIFFERENT persona's history (for cross-persona notifications)
  const pushNotification = useCallback((targetPersona, msg) => {
    _update(targetPersona, prev => [...prev, msg]);
    // Only bump the badge if target is not already the active persona
    setLocalNotifCounts(prev => ({
      ...prev,
      [targetPersona]: (prev[targetPersona] || 0) + 1,
    }));
    // The local message gives immediate feedback; the backend copy makes the
    // notification available to another persona/browser after refresh.
    postPersonaNotification(targetPersona, msg).catch(() => {});
  }, [_update]);

  const setTheme = useCallback((t) => {
    setThemeState(t);
    localStorage.setItem('dro-theme', t);
  }, []);

  const refreshNotifCounts = useCallback(() => {
    getNotifCounts().then(setNotifCounts).catch(() => {});
  }, []);

  const refreshWorkOrders = useCallback(() => {
    fetchWorkOrders().then(payload => {
      const rows = mergeWOs(payload);
      if (rows.length) {
        setWorkOrders(prev => {
          const hasNew = rows.length !== prev.length ||
            rows.some(r => { const ex = prev.find(p => p.id === r.id); return !ex || ex.st !== r.st; });
          return hasNew ? rows : prev;
        });
      }
    }).catch(() => {});
  }, []);

  const _PERSONA_DISPLAY = {
    supervisor:  'Plant Supervisor',
    engineer:    'Reliability Engineer',
    maintenance: 'Maintenance Planner',
    manager:     'Plant Manager',
    executive:   'Executive',
    ot:          'OT Controls Operator',
    safety:      'Safety Officer',
    md:          'Managing Director',
  };

  const updateWOStatus = useCallback((id, newStatus, actingPersona, decision = {}) => {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const personaLabel = _PERSONA_DISPLAY[actingPersona] || actingPersona || 'Unknown';
    const dotColor = newStatus === 'Scheduled'   ? 'var(--gn)'
      : newStatus === 'In Progress' ? 'var(--ac2)'
      : newStatus === 'Closed'      ? 'var(--t3)'
      : 'var(--am)';
    const timeEntry = { dot: dotColor, ev: `Status → ${newStatus}`, by: personaLabel, tm: `Today ${now}` };
    const applyUpdate = wo => wo.id === id
      ? { ...wo, st: newStatus, tl: [timeEntry, ...(wo.tl || [])] }
      : wo;

    setWorkOrders(prev => {
      // Keep the visible fleet state aligned with the durable WO lifecycle
      // immediately; the next dashboard poll rehydrates the same value.
      if (newStatus === 'Rejected') {
        const wo = prev.find(w => w.id === id);
        if (wo?.as) updateAssetStatus(wo.as, 'critical');
      } else if (['Pending', 'Scheduled', 'In Progress', 'Approved'].includes(newStatus)) {
        const wo = prev.find(w => w.id === id);
        if (wo?.as) updateAssetStatus(wo.as, 'warning');
      }
      // When WO is closed, mark the associated asset as healthy (maintenance complete)
      if (newStatus === 'Closed') {
        const wo = prev.find(w => w.id === id);
        if (wo?.as) {
          const applyHealthy = a =>
            (a.id === wo.as || a.asset_id === wo.as) ? { ...a, st: 'healthy' } : a;
          setAssets(currentAssets => {
            return currentAssets.map(applyHealthy);
          });
          setSelectedAsset(sel => sel ? applyHealthy(sel) : sel);
          // Mark the corresponding pipeline state entry as WO closed so AgentDetail shows "Completed"
          setAssetPipelineState(prevPipe => {
            let changed = false;
            const next = { ...prevPipe };
            Object.keys(next).forEach(key => {
              if (next[key].woId === id) {
                next[key] = { ...next[key], woClosed: true, woClosedBy: actingPersona };
                changed = true;
              }
            });
            if (!changed) return prevPipe;
            saveAssetPipeline(next);
            return next;
          });
        }
      }
      return prev.map(applyUpdate);
    });
    setSelectedWO(prev => prev?.id === id ? applyUpdate(prev) : prev);
    patchWorkOrder(id, {
      status: newStatus,
      updated_by: personaLabel,
      persona: actingPersona,
      ...(newStatus === 'Rejected' ? {
        reason_code: decision.reasonCode || 'other',
        free_text: decision.freeText || '',
      } : {}),
    }).then(serverRow => {
      // Keep Agent 8's decision visible after the asynchronous backend update
      // and after the next work-order poll/reload.
      if (!serverRow || !serverRow.agent8_learning) return;
      setWorkOrders(prev => prev.map(row => row.id === id
        ? { ...row, agent8_learning: serverRow.agent8_learning,
            rejection_reason_code: serverRow.rejection_reason_code || row.rejection_reason_code,
            rejection_reason: serverRow.rejection_reason || row.rejection_reason }
        : row));
      setSelectedWO(prev => prev?.id === id
        ? { ...prev, agent8_learning: serverRow.agent8_learning,
            rejection_reason_code: serverRow.rejection_reason_code || prev.rejection_reason_code,
            rejection_reason: serverRow.rejection_reason || prev.rejection_reason }
        : prev);
    }).catch(() => {});
  }, []);

  return (
    <AppContext.Provider value={{
      persona, setPersona,
      currentView, setCurrentView,
      agentAsset, setAgentAsset,
      assets, assetsLoaded, workOrders, dataStatus,
      updateAssetStatus, updateAssetSensorData,
      selectedAsset, setSelectedAsset,
      selectedWO, setSelectedWO,
      selectedNode, setSelectedNode,
      currentAgTab, setCurrentAgTab,
      messages, messagesMap, setMessages, addMessage, clearMessages, patchMessage, pushNotification,
      refreshWorkOrders,
      pipelineRunning, setPipelineRunning,
      theme, setTheme,
      notifCounts, refreshNotifCounts, localNotifCounts, clearLocalNotif,
      showLeftPanel, setShowLeftPanel,
      updateWOStatus,
      assetPipelineState, updateAssetPipelineState,
    }}>
      {children}
    </AppContext.Provider>
  );
}
