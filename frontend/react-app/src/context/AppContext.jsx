import React, { createContext, useState, useCallback, useEffect } from 'react';
import { ASSETS } from '../data/assets';
import { WOS } from '../data/workOrders';
import { getNotifCounts } from '../api/pipeline';
import { fetchDashboardAssets } from '../api/dashboard';
import { fetchWorkOrders, patchWorkOrder } from '../api/workOrders';
import { useAuth } from './AuthContext';
import { activeEscalationStep, remainingSeconds } from '../utils/escalationTimer';

export const AppContext = createContext(null);

const SESSION_KEY = 'dro-chat';

const WO_FALLBACK = Object.fromEntries(WOS.map(wo => [wo.id, wo]));

function mergeWOs(payload) {
  return (payload.workorders || []).map(wo => {
    const fb = WO_FALLBACK[wo.id] || {};
    return {
      ...fb,
      id: wo.id,
      pr: wo.priority || 'MEDIUM',
      st: wo.status || 'Pending',
      ti: wo.title || wo.description || wo.id,
      as: wo.asset_display || wo.asset_id || fb.as || '',
      asgn: wo.assigned_to || 'Pending',
      due: wo.due || 'Not scheduled',
      by: wo.created_by || 'DRO Agent',
      tp: wo.type || 'Maintenance',
      est: wo.est_hours ? `${wo.est_hours}h` : (fb.est || ''),
      parts: wo.parts || fb.parts || '',
      rul: wo.rul ?? fb.rul ?? '—',
      risk: wo.risk ?? fb.risk ?? '—',
      cost: wo.cost ?? fb.cost ?? '—',
      cl: (wo.checklist || []).map(item => ({ ck: Boolean(item.done), tx: item.text, tg: item.tag || 'Task' })),
      tl: wo.timeline?.length
        ? wo.timeline.map(ev => ({ dot: ev.dot || 'var(--t3)', ev: ev.ev, tm: ev.tm }))
        : (fb.tl || []),
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

export function AppProvider({ children }) {
  const { user } = useAuth();
  const defaultPersona = user?.allowed_personas?.[0] || 'supervisor';
  const [persona, setPersona] = useState(defaultPersona);
  const [currentView, setCurrentView] = useState('chat');
  const [agentAsset, setAgentAsset] = useState('M-104');
  const [assets, setAssets] = useState(ASSETS);
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
          faults: (a.faults || []).map(f => ({
            dt: f.date, ty: f.type, st: `${f.stage} ${f.status}`,
            sc: a.status === 'critical' ? 'var(--rd)' : 'var(--am)',
          })),
          backend_source: a.source,
        };
      });
      if (rows.length) {
        setAssets(rows);
        setSelectedAsset(current => rows.find(a => a.id === current?.id) || rows[0]);
        setDataStatus(current => ({ ...current, assets: payload.source || 'backend' }));
      }
    }).catch(() => setDataStatus(current => ({ ...current, assets: 'fallback' })));

    fetchWorkOrders().then(payload => {
      if (!active) return;
      const rows = mergeWOs(payload);
      if (rows.length) {
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

  // A4 escalation worker scans every persona inbox, not only the card currently
  // visible on screen. Expired approvals therefore transfer after persona
  // switches and immediately after a session is restored.
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
            const escalation = message.rec?.decision_support?.approval_escalation;
            const step = activeEscalationStep(escalation, stageIndex);
            if (!step || remainingSeconds(step.escalates_at_utc, nowMs) !== 0) {
              return message;
            }
            changed = true;
            const transferId = `${message.id}-escalation-${stageIndex + 1}`;
            transfers.push({
              target: step.to_persona_id,
              message: {
                id: transferId,
                type: 'hitl_executor',
                rec: message.rec,
                escalationStageIndex: stageIndex + 1,
                escalatedFrom: step.from_persona_name,
                time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              },
            });
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

  // Push a message into a DIFFERENT persona's history (for cross-persona notifications)
  const pushNotification = useCallback((targetPersona, msg) => {
    _update(targetPersona, prev => [...prev, msg]);
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

  const updateWOStatus = useCallback((id, newStatus, actingPersona) => {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const personaLabel = _PERSONA_DISPLAY[actingPersona] || actingPersona || 'Unknown';
    const dotColor = newStatus === 'Scheduled' ? 'var(--gn)'
      : newStatus === 'In Progress' ? 'var(--ac2)'
      : newStatus === 'Closed' ? 'var(--t3)'
      : 'var(--am)';
    const timeEntry = { dot: dotColor, ev: `Status → ${newStatus}`, by: personaLabel, tm: `Today ${now}` };
    const applyUpdate = wo => wo.id === id
      ? { ...wo, st: newStatus, tl: [timeEntry, ...(wo.tl || [])] }
      : wo;
    setWorkOrders(prev => prev.map(applyUpdate));
    setSelectedWO(prev => prev?.id === id ? applyUpdate(prev) : prev);
    patchWorkOrder(id, { status: newStatus, updated_by: personaLabel }).catch(() => {});
  }, []);

  return (
    <AppContext.Provider value={{
      persona, setPersona,
      currentView, setCurrentView,
      agentAsset, setAgentAsset,
      assets, workOrders, dataStatus,
      selectedAsset, setSelectedAsset,
      selectedWO, setSelectedWO,
      selectedNode, setSelectedNode,
      currentAgTab, setCurrentAgTab,
      messages, messagesMap, setMessages, addMessage, clearMessages, patchMessage, pushNotification,
      refreshWorkOrders,
      pipelineRunning, setPipelineRunning,
      theme, setTheme,
      notifCounts, refreshNotifCounts,
      showLeftPanel, setShowLeftPanel,
      updateWOStatus,
    }}>
      {children}
    </AppContext.Provider>
  );
}
